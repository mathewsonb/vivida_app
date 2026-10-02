const CLIENT_ID_KEY = 'vibe_client_id';

/**
 * Synchronously retrieves or initializes a persistent unique Client ID.
 * Prevents race conditions during initial API calls.
 */
export function getOrCreateClientId() {
  try {
    let clientId = localStorage.getItem(CLIENT_ID_KEY);
    if (!clientId) {
      clientId = typeof crypto.randomUUID === 'function' 
        ? crypto.randomUUID() 
        : `client_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;
      localStorage.setItem(CLIENT_ID_KEY, clientId);
    }
    return clientId;
  } catch (err) {
    console.warn('localStorage unavailable, generating fallback session ID:', err);
    return `session_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;
  }
}