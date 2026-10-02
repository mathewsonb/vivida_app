// src/services/historyService.js
import { supabase } from '../lib/supabaseClient';
import { getOrCreateClientId } from '../utils/session';

const SALT = import.meta.env.VITE_APP_SALT || 'vivida_privacy_salt_2025';
const VECTOR_DRIFT_ALPHA = 0.1; // Learning rate
const MOOD_STORAGE_KEY = 'vivida_user_mood_vector';
const HISTORY_STORAGE_KEY = 'vibe_history';

// Default baseline mood vector
const DEFAULT_MOOD = { energy: 0.5, social: 0.5, novelty: 0.5 };

/**
 * Client-side SHA-256 hash generator for email privacy
 */
export async function hashEmail(email) {
  if (!email || !email.trim()) return null;
  try {
    const encoder = new TextEncoder();
    const data = encoder.encode(email.trim().toLowerCase() + SALT);
    const hashBuffer = await crypto.subtle.digest('SHA-256', data);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    return hashArray.map((b) => b.toString(16).padStart(2, '0')).join('');
  } catch (err) {
    console.error('SHA-256 calculation error:', err);
    return null;
  }
}

/**
 * Reads and validates current mood vector directly from LocalStorage.
 */
export function getCurrentMood() {
  try {
    const saved = localStorage.getItem(MOOD_STORAGE_KEY);
    if (saved) {
      const parsed = JSON.parse(saved);
      if (parsed && typeof parsed === 'object') {
        return {
          energy: Number(parsed.energy ?? DEFAULT_MOOD.energy),
          social: Number(parsed.social ?? DEFAULT_MOOD.social),
          novelty: Number(parsed.novelty ?? DEFAULT_MOOD.novelty),
        };
      }
    }
  } catch (err) {
    console.warn('Failed to read mood vector from localStorage:', err);
  }
  return { ...DEFAULT_MOOD };
}

/**
 * Persists an updated or initial mood vector to LocalStorage.
 */
export function setCurrentMood(newMood) {
  const validMood = {
    energy: Number(newMood?.energy ?? DEFAULT_MOOD.energy),
    social: Number(newMood?.social ?? DEFAULT_MOOD.social),
    novelty: Number(newMood?.novelty ?? DEFAULT_MOOD.novelty),
  };
  try {
    localStorage.setItem(MOOD_STORAGE_KEY, JSON.stringify(validMood));
  } catch (err) {
    console.warn('Failed to persist mood vector:', err);
  }
  return validMood;
}

/**
 * Calculates dynamic vector convergence based on signed element-wise update:
 * u_{t+1} = u_t + alpha * weight * (e_t - u_t)
 */
export function applyVectorDrift(eventVector, action, currentMood) {
  const safeMood = currentMood || getCurrentMood();

  const weights = {
    'not_my_scene': -1.0,
    'maybe_later': 0.0,
    'totally_vibe': 1.0
  };

  const weight = weights[action] ?? 0.0;
  if (weight === 0.0 || !eventVector) {
    return safeMood;
  }

  const keys = ['energy', 'social', 'novelty'];
  const updatedMood = { ...safeMood };
  const driftDetails = {};

  keys.forEach((key) => {
    const uVal = Number(safeMood[key] ?? 0.5);
    const eVal = Number(eventVector?.[key] ?? eventVector?.[`${key}_vector`] ?? 0.5);

    const delta = eVal - uVal;
    const shift = VECTOR_DRIFT_ALPHA * weight * delta;
    const rawVal = uVal + shift;
    const clampedVal = Math.max(0.0, Math.min(1.0, Number(rawVal.toFixed(4))));

    updatedMood[key] = clampedVal;
    driftDetails[key] = {
      userInitial: uVal,
      eventTarget: eVal,
      shift: Number(shift.toFixed(4)),
      final: clampedVal
    };
  });

  // Explicit console verification log
  console.groupCollapsed(`🎯 [Vector Drift Log] Action: "${action}" | Weight: ${weight}`);
  console.log('Event Data:', eventVector);
  console.log('Initial Mood Vector:', safeMood);
  console.table(driftDetails);
  console.log('Final Computed Mood Vector:', updatedMood);
  console.groupEnd();

  // Save updated vector back to local storage
  setCurrentMood(updatedMood);
  return updatedMood;
}

/**
 * Log a card swipe action to Supabase & LocalStorage asynchronously
 */
export async function recordSwipe(action, event, userEmail = null, calculatedMood = null) {
  const clientId = getOrCreateClientId();
  const userHash = userEmail ? await hashEmail(userEmail) : clientId;

  // Use the pre-calculated mood if passed, otherwise compute it
  const newMoodVector = calculatedMood || applyVectorDrift(event, action);

  // LocalStorage entry sync
  try {
    const localHistory = JSON.parse(localStorage.getItem(HISTORY_STORAGE_KEY) || '[]');
    const existingIndex = localHistory.findIndex((item) => item.event_id === event.id);
    const entry = {
      id: typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `hist_${Date.now()}`,
      user_id: userHash,
      event_id: event.id,
      action: action,
      created_at: new Date().toISOString(),
      events: event,
      mood_snapshot: newMoodVector,
    };

    if (existingIndex >= 0) {
      localHistory[existingIndex] = entry;
    } else {
      localHistory.unshift(entry);
    }
    localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(localHistory));
  } catch (err) {
    console.warn('LocalStorage save failed:', err);
  }

  // Supabase DB persistence
  if (supabase) {
    supabase.from('user_interactions').upsert(
      {
        user_id: userHash,
        event_id: event.id,
        action: action,
        created_at: new Date().toISOString(),
        mood_snapshot: newMoodVector,
      },
      { onConflict: 'user_id,event_id' }
    ).then(({ error }) => {
      if (error) console.error('Supabase write error:', error.message);
    }).catch(err => console.error('Supabase async transaction error:', err));
  }

  return newMoodVector;
}

/**
 * Fetch active, unexpired user saved events ("View My Past Events")
 */
export async function getUserHistory(userEmail = null) {
  const clientId = getOrCreateClientId();
  const userHash = userEmail ? await hashEmail(userEmail) : clientId;

  if (supabase) {
    try {
      const { data, error } = await supabase
        .from('user_interactions')
        .select(`
          id,
          action,
          created_at,
          events!inner (*)
        `)
        .eq('user_id', userHash)
        .in('action', ['totally_vibe', 'maybe_later'])
        .order('created_at', { ascending: false });

      if (!error && data) {
        const todayStr = new Date().toISOString().split('T')[0];
        return data.filter((item) => {
          const endDate = item.events?.end_date || item.events?.date;
          if (!endDate) return true;
          return endDate >= todayStr;
        });
      }
    } catch (err) {
      console.warn('Supabase lookup failed, falling back to local history:', err);
    }
  }

  // LocalStorage Fallback
  try {
    const todayStr = new Date().toISOString().split('T')[0];
    const localHistory = JSON.parse(localStorage.getItem(HISTORY_STORAGE_KEY) || '[]');

    return localHistory.filter((item) => {
      const isMatch = item.user_id === userHash && ['totally_vibe', 'maybe_later'].includes(item.action);
      const endDate = item.events?.end_date || item.events?.date;
      return isMatch && (!endDate || endDate >= todayStr);
    });
  } catch (err) {
    return [];
  }
}