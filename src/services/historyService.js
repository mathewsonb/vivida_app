// src/services/historyService.js
import { supabase } from '../lib/supabaseClient';
import { getOrCreateClientId } from '../utils/session';

const SALT = import.meta.env.VITE_APP_SALT || 'vivida_privacy_salt_2025';
const VECTOR_DRIFT_ALPHA = 0.03; // Learning rate
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
    'totally_vibe': 1.0,
    'interested': 1.0
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

  console.groupCollapsed(`🎯 [Vector Drift Log] Action: "${action}" | Weight: ${weight}`);
  console.log('Event Data:', eventVector);
  console.log('Initial Mood Vector:', safeMood);
  console.table(driftDetails);
  console.log('Final Computed Mood Vector:', updatedMood);
  console.groupEnd();

  setCurrentMood(updatedMood);
  return updatedMood;
}

/**
 * Log a card swipe action to Supabase & LocalStorage asynchronously with full vector snapshots.
 */
export async function recordSwipe(action, event, userEmail = null, calculatedMood = null) {
  const clientId = getOrCreateClientId();
  const storedHash = localStorage.getItem('vivida_user_hash');
  const userHash = userEmail ? (userEmail.length === 64 ? userEmail : await hashEmail(userEmail)) : (storedHash || clientId);
  
  const newMoodVector = calculatedMood || applyVectorDrift(event, action);

  const eEnergy = Number(event?.energy ?? event?.energy_vector ?? 0.5);
  const eSocial = Number(event?.social ?? event?.social_vector ?? 0.5);
  const eNovelty = Number(event?.novelty ?? event?.novelty_vector ?? 0.5);

  const entryPayload = {
    id: typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `hist_${Date.now()}`,
    user_id: userHash,
    event_id: String(event.id).trim().toLowerCase(),
    action: action,
    created_at: new Date().toISOString(),
    mood_snapshot: newMoodVector,
    energy_vector: eEnergy,
    social_vector: eSocial,
    novelty_vector: eNovelty,
    event_snapshot: {
      title: event.title,
      category: event.category,
      venue_name: event.venue_name || event.venue,
      address: event.address,
      schedule: event.schedule,
      date: event.date,
      end_date: event.end_date,
      is_free: event.is_free,
      indoor_outdoor: event.indoor_outdoor,
      image_url: event.image_url,
      external_url: event.external_url
    },
    events: event
  };

  // LocalStorage entry sync
  try {
    const localHistory = JSON.parse(localStorage.getItem(HISTORY_STORAGE_KEY) || '[]');
    const existingIndex = localHistory.findIndex((item) => String(item.event_id).trim().toLowerCase() === String(event.id).trim().toLowerCase());

    if (existingIndex >= 0) {
      localHistory[existingIndex] = entryPayload;
    } else {
      localHistory.unshift(entryPayload);
    }
    localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(localHistory));
  } catch (err) {
    console.warn('LocalStorage save failed:', err);
  }

  // Supabase DB persistence
  if (supabase) {
    const dbRecord = {
      user_id: userHash,
      event_id: String(event.id).trim().toLowerCase(),
      action: action,
      created_at: entryPayload.created_at,
      mood_snapshot: newMoodVector,
      energy_vector: eEnergy,
      social_vector: eSocial,
      novelty_vector: eNovelty,
      event_snapshot: entryPayload.event_snapshot
    };

    supabase.from('user_interactions').upsert(
      dbRecord,
      { onConflict: 'user_id,event_id' }
    ).then(({ error }) => {
      if (error) console.error('Supabase write error:', error.message);
    }).catch(err => console.error('Supabase async transaction error:', err));
  }

  return newMoodVector;
}

/**
 * Fetch all user saved events ("View My Past Events") matching HistoryPage exactly
 */
export async function getUserHistory(userEmail = null) {
  const clientId = getOrCreateClientId();
  const storedHash = localStorage.getItem('vivida_user_hash');
  const userHash = userEmail ? (userEmail.length === 64 ? userEmail : await hashEmail(userEmail)) : (storedHash || clientId);

  let remoteInteractions = [];

  if (supabase) {
    try {
      const { data, error } = await supabase
        .from('user_interactions')
        .select('*')
        .eq('user_id', userHash)
        .in('action', ['totally_vibe', 'maybe_later', 'interested'])
        .order('created_at', { ascending: false });

      if (!error && data) {
        remoteInteractions = data;
      }
    } catch (err) {
      console.warn('Supabase history lookup failed:', err);
    }
  }

  const eventIds = [...new Set(remoteInteractions.map((i) => String(i.event_id).trim().toLowerCase()))];
  let activeEventsMap = new Map();

  if (eventIds.length > 0 && supabase) {
    try {
      const { data: eventsData } = await supabase
        .from('events')
        .select('*')
        .in('id', eventIds);

      if (eventsData) {
        eventsData.forEach((e) => {
          activeEventsMap.set(String(e.id).trim().toLowerCase(), e);
        });
      }
    } catch (err) {
      console.warn('Failed to fetch active events for history hydration:', err);
    }
  }

  let localHistory = [];
  try {
    localHistory = JSON.parse(localStorage.getItem(HISTORY_STORAGE_KEY) || '[]');
  } catch (err) {
    console.warn('Failed to read vibe_history:', err);
  }

  const combinedMap = new Map();

  // 1. Process Local History
  localHistory.forEach((item) => {
    if (['totally_vibe', 'maybe_later', 'interested'].includes(item.action)) {
      const rawId = item.event_id || item.events?.id || item.id;
      if (rawId) {
        const idKey = String(rawId).trim().toLowerCase();
        combinedMap.set(idKey, {
          ...item,
          events: item.events || item.event_snapshot || {}
        });
      }
    }
  });

  // 2. Process Remote Database History
  remoteInteractions.forEach((item) => {
    const idKey = String(item.event_id).trim().toLowerCase();
    const activeEvent = activeEventsMap.get(idKey);
    combinedMap.set(idKey, {
      ...item,
      events: activeEvent || item.event_snapshot || { id: item.event_id }
    });
  });

  // Return all combined records so the badge count mirrors HistoryPage exactly
  return Array.from(combinedMap.values());
}

/**
 * Retrieves past user interactions corresponding to current or upcoming events.
 */
export async function getFutureUserInteractions(userIdentifier = null) {
  if (!supabase) return [];

  const storedHash = localStorage.getItem('vivida_user_hash');
  const activeKey = userIdentifier || storedHash || getOrCreateClientId();

  const now = new Date();
  const todayStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;

  try {
    const { data: interactions, error: intError } = await supabase
      .from('user_interactions')
      .select('*')
      .eq('user_id', activeKey)
      .order('created_at', { ascending: false });

    if (intError || !interactions || interactions.length === 0) return [];

    const eventIds = [...new Set(interactions.map((i) => String(i.event_id).trim().toLowerCase()))];
    const { data: events } = await supabase
      .from('events')
      .select('*')
      .in('id', eventIds);

    const eventMap = new Map();
    if (events) {
      events.forEach((e) => {
        eventMap.set(String(e.id).trim().toLowerCase(), e);
      });
    }

    const seenEventIds = new Set();
    const futureInteractions = [];

    for (const item of interactions) {
      const eventIdStr = String(item.event_id).trim().toLowerCase();
      if (seenEventIds.has(eventIdStr)) continue;

      const activeEvent = eventMap.get(eventIdStr);
      const evData = activeEvent || item.event_snapshot || {};

      const schedule = evData.schedule;
      const endRaw = evData.end_date || evData.date;
      const isFuture = !schedule 
        ? (!endRaw || String(endRaw) >= todayStr)
        : (String(schedule).includes(todayStr) || String(schedule) >= todayStr);

      if (isFuture) {
        seenEventIds.add(eventIdStr);
        futureInteractions.push({
          ...item,
          events: evData
        });
      }
    }

    return futureInteractions;
  } catch (err) {
    console.warn('Failed to retrieve future user interactions:', err);
    return [];
  }
}