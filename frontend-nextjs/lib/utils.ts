import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Dashboard utility functions for metrics formatting
 */

/**
 * Format percentage trend with appropriate styling
 */
export const formatTrend = (value: number | undefined, period: 'month' | 'week' | 'day' = 'week') => {
  if (value === undefined || value === null) {
    return { text: 'No data', className: 'text-gray-500' };
  }
  
  const prefix = value > 0 ? '+' : '';
  const className = value > 0 ? 'text-green-600' : 
                    value < 0 ? 'text-red-600' : 
                    'text-gray-600';
  
  return { 
    text: `${prefix}${value.toFixed(1)}% this ${period}`, 
    className 
  };
};

/**
 * Format accuracy percentage
 */
export const formatAccuracy = (accuracy: number | undefined) => {
  if (accuracy === undefined || accuracy === null) {
    return 'N/A';
  }
  return `${accuracy.toFixed(1)}% accuracy`;
};

/**
 * Format online user count
 */
export const formatOnlineCount = (count: number | undefined) => {
  if (count === undefined || count === null) {
    return 'Status unknown';
  }
  if (count === 0) return 'No users online';
  if (count === 1) return '1 user online now';
  return `${count} online now`;
};

/**
 * Format trend percentage only (without period text)
 */
export const formatTrendValue = (value: number | undefined) => {
  if (value === undefined || value === null) return 'N/A';
  const prefix = value > 0 ? '+' : '';
  return `${prefix}${value.toFixed(1)}%`;
};