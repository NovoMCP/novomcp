/**
 * Error Tracking and Analytics Integration
 * Captures and reports errors to Sentry and custom analytics
 */

interface ErrorContext {
  user?: {
    id: string;
    email: string;
    org: string;
  };
  component?: string;
  action?: string;
  extra?: Record<string, any>;
}

class ErrorTracker {
  private initialized = false;
  private queue: Array<{ error: Error; context?: ErrorContext }> = [];
  private analyticsEndpoint = process.env.NEXT_PUBLIC_API_URL + '/analytics/errors';

  constructor() {
    if (typeof window !== 'undefined') {
      this.initialize();
    }
  }

  private async initialize() {
    // Initialize Sentry if DSN is provided
    const sentryDSN = process.env.NEXT_PUBLIC_SENTRY_DSN;
    if (sentryDSN) {
      // TODO: Uncomment when @sentry/nextjs is installed
      // try {
      //   const Sentry = await import('@sentry/nextjs');
      //   
      //   Sentry.init({
      //     dsn: sentryDSN,
      //     environment: process.env.NEXT_PUBLIC_ENVIRONMENT || 'development',
      //     tracesSampleRate: process.env.NODE_ENV === 'production' ? 0.1 : 1.0,
      //     beforeSend(event, hint) {
      //       // Filter out known non-issues
      //       if (event.exception?.values?.[0]?.value?.includes('ResizeObserver')) {
      //         return null;
      //       }
      //       if (event.exception?.values?.[0]?.value?.includes('Non-Error promise rejection')) {
      //         return null;
      //       }
      //       return event;
      //     },
      //     integrations: [
      //       new Sentry.BrowserTracing(),
      //       new Sentry.Replay({
      //         maskAllText: true,
      //         blockAllMedia: true,
      //       }),
      //     ],
      //     replaysSessionSampleRate: 0.1,
      //     replaysOnErrorSampleRate: 1.0,
      //   });
      //
      //   this.initialized = true;
      //
      //   // Process queued errors
      //   this.queue.forEach(({ error, context }) => {
      //     this.captureError(error, context);
      //   });
      //   this.queue = [];
      //
      // } catch (error) {
      //   console.error('Failed to initialize Sentry:', error);
      // }
      console.log('Sentry DSN found but Sentry package not installed yet');
      this.initialized = true;
    }

    // Setup global error handlers
    this.setupGlobalHandlers();

    // Initialize Google Analytics
    this.initializeGoogleAnalytics();
  }

  private setupGlobalHandlers() {
    // Unhandled promise rejections
    window.addEventListener('unhandledrejection', (event) => {
      this.captureError(
        new Error(`Unhandled Promise Rejection: ${event.reason}`),
        {
          extra: {
            promise: event.promise,
            reason: event.reason
          }
        }
      );
    });

    // Global error handler
    window.addEventListener('error', (event) => {
      this.captureError(
        event.error || new Error(event.message),
        {
          extra: {
            filename: event.filename,
            lineno: event.lineno,
            colno: event.colno
          }
        }
      );
    });

    // React Error Boundary errors are handled by ErrorBoundary component
  }

  private initializeGoogleAnalytics() {
    const gaId = process.env.NEXT_PUBLIC_GA_ID;
    if (!gaId) return;

    // Load Google Analytics
    const script = document.createElement('script');
    script.src = `https://www.googletagmanager.com/gtag/js?id=${gaId}`;
    script.async = true;
    document.head.appendChild(script);

    // Initialize gtag
    (window as any).dataLayer = (window as any).dataLayer || [];
    function gtag(...args: any[]) {
      (window as any).dataLayer.push(arguments);
    }
    (window as any).gtag = gtag;

    gtag('js', new Date());
    gtag('config', gaId, {
      page_path: window.location.pathname,
      debug_mode: process.env.NODE_ENV === 'development'
    });
  }

  public captureError(error: Error, context?: ErrorContext) {
    // Queue if not initialized
    if (!this.initialized) {
      this.queue.push({ error, context });
      return;
    }

    // TODO: Uncomment when @sentry/nextjs is installed
    // // Send to Sentry
    // if (typeof window !== 'undefined') {
    //   import('@sentry/nextjs').then((Sentry) => {
    //     Sentry.withScope((scope) => {
    //       if (context?.user) {
    //         scope.setUser({
    //           id: context.user.id,
    //           email: context.user.email,
    //           organization: context.user.org
    //         });
    //       }
    //
    //       if (context?.component) {
    //         scope.setTag('component', context.component);
    //       }
    //
    //       if (context?.action) {
    //         scope.setTag('action', context.action);
    //       }
    //
    //       if (context?.extra) {
    //         Object.entries(context.extra).forEach(([key, value]) => {
    //           scope.setExtra(key, value);
    //         });
    //       }
    //
    //       Sentry.captureException(error);
    //     });
    //   });
    // }

    // Send to custom analytics
    this.sendToAnalytics(error, context);

    // Log to console in development
    if (process.env.NODE_ENV === 'development') {
      console.error('[Error Tracker]', error, context);
    }
  }

  private async sendToAnalytics(error: Error, context?: ErrorContext) {
    try {
      await fetch(this.analyticsEndpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          message: error.message,
          stack: error.stack,
          context,
          url: window.location.href,
          userAgent: navigator.userAgent,
          timestamp: Date.now()
        })
      });
    } catch (err) {
      // Silently fail to not cause more errors
    }
  }

  public trackEvent(category: string, action: string, label?: string, value?: number) {
    // Send to Google Analytics
    if (typeof window !== 'undefined' && (window as any).gtag) {
      (window as any).gtag('event', action, {
        event_category: category,
        event_label: label,
        value: value
      });
    }

    // Send to custom analytics
    this.sendCustomEvent(category, action, label, value);
  }

  private async sendCustomEvent(category: string, action: string, label?: string, value?: number) {
    try {
      await fetch(`${process.env.NEXT_PUBLIC_API_URL}/analytics/events`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          category,
          action,
          label,
          value,
          timestamp: Date.now()
        })
      });
    } catch (error) {
      // Silently fail
    }
  }

  public setUser(user: { id: string; email: string; org: string }) {
    // TODO: Uncomment when @sentry/nextjs is installed
    // // Set user in Sentry
    // if (typeof window !== 'undefined') {
    //   import('@sentry/nextjs').then((Sentry) => {
    //     Sentry.setUser({
    //       id: user.id,
    //       email: user.email,
    //       organization: user.org
    //     });
    //   });
    // }

    // Set user in Google Analytics
    if (typeof window !== 'undefined' && (window as any).gtag) {
      (window as any).gtag('config', process.env.NEXT_PUBLIC_GA_ID, {
        user_id: user.id
      });
    }
  }

  public clearUser() {
    // TODO: Uncomment when @sentry/nextjs is installed
    // // Clear user in Sentry
    // if (typeof window !== 'undefined') {
    //   import('@sentry/nextjs').then((Sentry) => {
    //     Sentry.setUser(null);
    //   });
    // }
  }
}

// Export singleton instance
export const errorTracker = new ErrorTracker();

// React hook for error tracking
import { useCallback } from 'react';

export function useErrorHandler() {
  const captureError = useCallback((error: Error, context?: ErrorContext) => {
    errorTracker.captureError(error, context);
  }, []);

  const trackEvent = useCallback((category: string, action: string, label?: string, value?: number) => {
    errorTracker.trackEvent(category, action, label, value);
  }, []);

  return { captureError, trackEvent };
}