import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

// Merge conditional class names while resolving conflicting Tailwind utilities
// (e.g. a later `px-4` wins over an earlier `px-2`).
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
