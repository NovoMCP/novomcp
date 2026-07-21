/**
 * NovoMCP Client (Slim)
 * Retained: proxyCall, call, getStatus, handleResponse, generateRequestId
 */

export interface ProxyOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
  data?: any;
  headers?: Record<string, string>;
  params?: Record<string, string | number | boolean | undefined>;
}

export class EngineClient {
  private baseUrl: string;
  private getToken: () => Promise<string | null>;
  private engineApiKey: string;

  constructor(getToken: (() => Promise<string | null>) | string, engineApiKey?: string, baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8018') {
    if (typeof getToken === 'string') {
      this.getToken = async () => getToken;
    } else {
      this.getToken = getToken;
    }
    this.engineApiKey = engineApiKey || process.env.NEXT_PUBLIC_NOVOMCP_API_KEY || '';
    this.baseUrl = baseUrl;
  }

  async proxyCall<T>(service: string, endpoint: string, options: ProxyOptions = {}): Promise<T> {
    const url = new URL(`${this.baseUrl}/proxy/${service}${endpoint}`);
    if (options.params) {
      Object.entries(options.params).forEach(([key, value]) => {
        if (value !== undefined && value !== null) {
          url.searchParams.append(key, String(value));
        }
      });
    }
    const token = await this.getToken();
    if (!token) {
      throw new Error('No authentication token available');
    }

    const method = options.method || 'GET';
    const response = await fetch(url.toString(), {
      method,
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
        'X-API-Key': this.engineApiKey,
        ...options.headers
      },
      body:
        method !== 'GET' && options.data !== undefined
          ? JSON.stringify(options.data)
          : undefined
    });

    return await this.handleResponse<T>(response);
  }

  async call<T>(endpoint: string, options: ProxyOptions = {}): Promise<T> {
    const token = await this.getToken();
    if (!token) {
      throw new Error('No authentication token available');
    }

    const normalisedEndpoint = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
    const needsPrefix = !normalisedEndpoint.startsWith('/novomcp') && !normalisedEndpoint.startsWith('/proxy') && !normalisedEndpoint.startsWith('/health') && !normalisedEndpoint.startsWith('/docs');
    const urlPath = needsPrefix ? `/novomcp${normalisedEndpoint}` : normalisedEndpoint;
    const url = new URL(`${this.baseUrl}${urlPath}`);
    if (options.params) {
      Object.entries(options.params).forEach(([key, value]) => {
        if (value !== undefined && value !== null) {
          url.searchParams.append(key, String(value));
        }
      });
    }

    const method = options.method || 'GET';

    const response = await fetch(url.toString(), {
      method,
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
        'X-API-Key': this.engineApiKey,
        ...options.headers,
      },
      body:
        method !== 'GET' && options.data !== undefined
          ? JSON.stringify(options.data)
          : undefined,
    });

    return this.handleResponse<T>(response);
  }

  async getStatus(): Promise<any> {
    const token = await this.getToken();
    if (!token) {
      throw new Error('No authentication token available');
    }

    const response = await fetch(`${this.baseUrl}/status`, {
      headers: {
        'Authorization': `Bearer ${token}`,
        'X-API-Key': this.engineApiKey
      }
    });

    return this.handleResponse(response);
  }

  private async handleResponse<T>(response: Response): Promise<T> {
    if (!response.ok) {
      let errorMessage = '';
      try {
        const errorData = await response.json();
        errorMessage = errorData.message || errorData.error || errorData.detail || 'Unknown error';
      } catch {
        errorMessage = await response.text() || `HTTP ${response.status} error`;
      }
      throw new Error(`API Error (${response.status}): ${errorMessage}`);
    }

    try {
      return await response.json();
    } catch {
      throw new Error('Invalid JSON response from server');
    }
  }

  generateRequestId(): string {
    return `req_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
  }
}

// Singleton
let engineClient: EngineClient | null = null;

export function getEngineClient(tokenOrGetter?: string | (() => Promise<string | null>), engineApiKey?: string): EngineClient {
  if (!engineClient && tokenOrGetter) {
    engineClient = new EngineClient(tokenOrGetter, engineApiKey);
  }
  if (!engineClient) {
    throw new Error('NovoMCP client not initialized. Provide JWT token.');
  }
  return engineClient;
}

export function initializeQuantaClient(tokenOrGetter: string | (() => Promise<string | null>), engineApiKey?: string, baseUrl?: string): EngineClient {
  engineClient = new EngineClient(tokenOrGetter, engineApiKey, baseUrl);
  return engineClient;
}
