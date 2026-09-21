// src/lib/crypto.js
const APP_SALT = import.meta.env.VITE_APP_SALT || 'vivida_default_salt_2026';

/**
 * Normalizes email and produces SHA-256 hash combined with APP_SALT
 * @param {string} email
 * @returns {Promise<string>} 64-character hex hash string
 */
export async function hashEmail(email) {
  if (!email) return '';

  // 1. Lowercase and Trim
  const normalized = email.trim().toLowerCase();
  
  // 2. Append Salt
  const saltedInput = `${normalized}${APP_SALT}`;

  // 3. SHA-256 Digest via Web Crypto API
  const encoder = new TextEncoder();
  const data = encoder.encode(saltedInput);
  const hashBuffer = await crypto.subtle.digest('SHA-256', data);

  // 4. Convert ArrayBuffer to Hex String
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map((b) => b.toString(16).padStart(2, '0')).join('');
}