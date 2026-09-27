// src/utils/session.js
export function getOrCreateClientId() {
  let clientId = localStorage.getItem('vibe_client_id');
  if (!clientId) {
    clientId = crypto.randomUUID();
    localStorage.setItem('vibe_client_id', clientId);
  }
  return clientId;
}