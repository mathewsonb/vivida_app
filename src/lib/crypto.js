/**
 * Hashes a string (e.g., email) using SHA-256 with a static salt.
 * Uses Web Crypto API (crypto.subtle).
 */
export async function hashEmail(email) {
  if (!email || typeof email !== 'string') return null;
  
  const cleanEmail = email.trim().toLowerCase();
  const salt = import.meta.env.VITE_APP_SALT || 'vivida_default_salt';
  const encoder = new TextEncoder();
  const data = encoder.encode(cleanEmail + salt);

  try {
    const hashBuffer = await crypto.subtle.digest('SHA-256', data);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    return hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
  } catch (err) {
    console.error('SHA-256 hashing failed:', err);
    return null;
  }
}

/**
 * Validates and sanitizes dynamic URLs to prevent XSS (e.g. javascript: URIs)
 */
export function sanitizeUrl(url) {
  if (!url || typeof url !== 'string') return '#';
  try {
    const parsed = new URL(url, window.location.origin);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
      return parsed.href;
    }
  } catch (e) {
    // Malformed URL
  }
  return '#';
}