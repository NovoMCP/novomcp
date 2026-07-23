'use client';

/**
 * NovoMCP Authentication Provider
 * Manages authentication state and JWT token storage
 */

import React, { createContext, useContext, useEffect, useState } from 'react';
import { EngineClient } from '@/core/orchestration/client';

export interface User {
  id: string;
  email: string;
  name: string;
  orgId: string;
  roles: string[];
  accessToken: string;
  refreshToken?: string;
  tokenExpiry?: number;
}

interface AuthContextType {
  user: User | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  loginWithEmail: (email: string, password: string) => Promise<{ success: boolean; error?: string }>;
  logout: () => Promise<void>;
  getAccessToken: () => Promise<string | null>;
  refreshToken: () => Promise<boolean>;
}

const AuthContext = createContext<AuthContextType | null>(null);

// API base URL — env-driven; local default so OSS clones talk to a
// locally-running engine. Set NEXT_PUBLIC_API_URL to point at a hosted engine.
const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8018';

export function useAuth(): AuthContextType {
  const context = useContext(AuthContext);
  if (!context) {
    // Return a dummy context during SSR/SSG
    if (typeof window === 'undefined') {
      return {
        user: null,
        isLoading: true,
        isAuthenticated: false,
        loginWithEmail: async () => ({ success: false }),
        logout: async () => {},
        getAccessToken: async () => null,
        refreshToken: async () => false,
      };
    }
    throw new Error('useAuth must be used within AuthProvider');
  }
  return context;
}

// OSS default: auth-less local single-user mode (the OSS split).
// Hosted deployments opt in to full auth by setting NEXT_PUBLIC_REQUIRE_AUTH=true.
const REQUIRE_AUTH = process.env.NEXT_PUBLIC_REQUIRE_AUTH === 'true';

// The synthetic local user injected in OSS mode. Grants full local access
// without login; the engine's LocalAuthGate accepts any bearer token.
const LOCAL_USER: User = {
  id: 'local',
  email: 'local@novomcp',
  name: 'Local User',
  orgId: 'local',
  roles: ['admin'],
  accessToken: 'local-dev',
  tokenExpiry: Number.MAX_SAFE_INTEGER,
};

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Check for existing session on mount
  useEffect(() => {
    const checkSession = () => {
      // Only access localStorage on the client side
      if (typeof window === 'undefined') {
        setIsLoading(false);
        return;
      }

      // OSS single-user mode: auto-provision the local user, skip login flow.
      if (!REQUIRE_AUTH) {
        setUser(LOCAL_USER);
        setIsLoading(false);
        return;
      }

      // Check if user data exists in localStorage
      const storedUser = localStorage.getItem('user');
      if (storedUser) {
        try {
          const userData = JSON.parse(storedUser);
          // Check if token is expired
          if (userData.tokenExpiry && Date.now() > userData.tokenExpiry) {
            console.log('Token expired, clearing session');
            localStorage.removeItem('user');
          } else {
            setUser(userData);
          }
        } catch (error) {
          console.error('Failed to parse stored user data:', error);
          localStorage.removeItem('user');
        }
      }
      setIsLoading(false);
    };

    checkSession();
  }, []);

  const loginWithEmail = async (email: string, password: string): Promise<{ success: boolean; error?: string }> => {
    try {
      setIsLoading(true);
      
      // Use NovoMCP proxy endpoint for authentication service
      const response = await fetch(`${API_BASE}/proxy/auth/auth/email-login`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-API-Key': process.env.NEXT_PUBLIC_NOVOMCP_API_KEY || ''
        },
        body: JSON.stringify({
          email: email,
          password: password
        })
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        return { 
          success: false, 
          error: errorData.message || errorData.detail || `Authentication failed: ${response.status}` 
        };
      }

      let result;
      const responseText = await response.text();
      console.log('Raw response:', responseText); // Debug logging
      
      try {
        result = JSON.parse(responseText);
      } catch (e) {
        console.error('Failed to parse response as JSON:', e);
        return {
          success: false,
          error: 'Invalid response format from server'
        };
      }
      
      console.log('Parsed response:', result); // Debug logging
      console.log('Response keys:', Object.keys(result || {})); // Log the keys
      console.log('Response type:', typeof result); // Log the type
      
      // Check multiple possible locations and key styles for the access token
      const accessToken = result.access_token
        || result.accessToken
        || result.token
        || result.data?.access_token
        || result.data?.accessToken
        || result.data?.token
        || result.body?.access_token
        || result.body?.accessToken
        || result.body?.token;
      
      if (!accessToken) {
        console.error('No access token found in response:', result);
        return {
          success: false,
          error: result.error || result.detail || result.message || 'Authentication failed - no access token received'
        };
      }
      
      const authData = {
        access_token: accessToken,
        refresh_token: result.refresh_token
          || result.refreshToken
          || result.data?.refresh_token
          || result.data?.refreshToken
          || result.body?.refresh_token
          || result.body?.refreshToken,
        expires_in: result.expires_in
          || result.data?.expires_in
          || result.body?.expires_in,
        user: result.user || result.data?.user || result.body?.user
      };
      
      // Now get user details through NovoMCP proxy
      const userResponse = await fetch(`${API_BASE}/proxy/managed backend/user/profile`, {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${authData.access_token}`,
          'X-API-Key': process.env.NEXT_PUBLIC_NOVOMCP_API_KEY || ''
        }
      });

      let userData: User;
      if (userResponse.ok) {
        const userResult = await userResponse.json();
        console.log('User profile response:', userResult); // Debug logging
        const userInfo = userResult.data || userResult;
        
        // Set user data from the response
        userData = {
          id: userInfo.id || authData.user?.id || authData.user?.sub,
          email: userInfo.email || authData.user?.email || email,
          name: userInfo.name || authData.user?.name || email.split('@')[0],
          orgId: userInfo.org_id || authData.user?.org_id || authData.user?.company_id || 'default',
          roles: userInfo.roles || authData.user?.roles || [authData.user?.role || 'user'],
          accessToken: authData.access_token,
          refreshToken: authData.refresh_token,
          tokenExpiry: Date.now() + ((authData.expires_in || 3600) * 1000)
        };
      } else {
        console.log('User profile fetch failed, using auth data'); // Debug logging
        // Fallback to auth data if profile fetch fails
        userData = {
          id: authData.user?.id || authData.user?.sub || email,
          email: authData.user?.email || email,
          name: authData.user?.name || email.split('@')[0],
          orgId: authData.user?.org_id || authData.user?.company_id || 'default',
          roles: authData.user?.roles || [authData.user?.role || 'user'],
          accessToken: authData.access_token,
          refreshToken: authData.refresh_token,
          tokenExpiry: Date.now() + ((authData.expires_in || 3600) * 1000)
        };
      }

      // Store user data in state and localStorage
      setUser(userData);
      if (typeof window !== 'undefined') {
        localStorage.setItem('user', JSON.stringify(userData));
      }

      return { success: true };
    } catch (error) {
      console.error('Email login failed:', error);
      return { 
        success: false, 
        error: error instanceof Error ? error.message : 'Login failed' 
      };
    } finally {
      setIsLoading(false);
    }
  };

  const logout = async () => {
    try {
      // Call logout through NovoMCP orchestration
      if (user?.accessToken) {
        await fetch(`${API_BASE}/proxy/auth/auth/logout`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${user.accessToken}`,
            'X-API-Key': process.env.NEXT_PUBLIC_NOVOMCP_API_KEY || ''
          }
        }).catch(console.error);
      }
      
      // Clear user data from state and localStorage
      setUser(null);
      if (typeof window !== 'undefined') {
        localStorage.removeItem('user');
      }
    } catch (error) {
      console.error('Logout failed:', error);
    }
  };

  const getAccessToken = async (): Promise<string | null> => {
    if (!user) return null;
    
    // Check if token is about to expire (within 5 minutes)
    if (user.tokenExpiry && Date.now() > user.tokenExpiry - 300000) {
      console.log('Token expiring soon, refreshing...');
      const refreshed = await refreshToken();
      if (!refreshed) {
        return null;
      }
    }
    
    return user?.accessToken || null;
  };

  const refreshToken = async (): Promise<boolean> => {
    if (!user?.refreshToken || isRefreshing) return false;
    
    try {
      setIsRefreshing(true);
      const response = await fetch(`${API_BASE}/proxy/auth/auth/refresh`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-API-Key': process.env.NEXT_PUBLIC_NOVOMCP_API_KEY || ''
        },
        body: JSON.stringify({
          refresh_token: user.refreshToken
        })
      });

      if (!response.ok) {
        console.error('Token refresh failed');
        await logout();
        return false;
      }

      const result = await response.json();
      const updatedUser = {
        ...user,
        accessToken: result.access_token
          || result.accessToken
          || result.token
          || result.data?.access_token
          || result.data?.accessToken
          || result.data?.token,
        refreshToken: result.refresh_token
          || result.refreshToken
          || result.data?.refresh_token
          || result.data?.refreshToken
          || user.refreshToken,
        tokenExpiry: Date.now() + (((result.expires_in
          || result.data?.expires_in
          || result.body?.expires_in
          || 3600)) * 1000)
      };

      setUser(updatedUser);
      if (typeof window !== 'undefined') {
        localStorage.setItem('user', JSON.stringify(updatedUser));
      }
      return true;
    } catch (error) {
      console.error('Token refresh error:', error);
      return false;
    } finally {
      setIsRefreshing(false);
    }
  };

  const value: AuthContextType = {
    user,
    isLoading,
    isAuthenticated: !!user,
    loginWithEmail,
    logout,
    getAccessToken,
    refreshToken,
  };

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
}

// Hook for NovoMCP integration
export function useEngine() {
  const { getAccessToken, isAuthenticated } = useAuth();
  
  const [client, setClient] = useState<EngineClient | null>(null);

  useEffect(() => {
    // Skip on server side
    if (typeof window === 'undefined') {
      return;
    }
    
    // Only create client if authenticated
    if (!isAuthenticated) {
      setClient(null);
      return;
    }

    // Create client with async token getter
    const engineApiKey = process.env.NEXT_PUBLIC_NOVOMCP_API_KEY || '';
    const engineClient = new EngineClient(getAccessToken, engineApiKey);
    setClient(engineClient);

    // Cleanup on unmount
    return () => {
      setClient(null);
    };
  }, [getAccessToken, isAuthenticated]);

  return client;
}
