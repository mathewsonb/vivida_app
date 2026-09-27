// src/components/SwipeDeck.jsx

import React from 'react';
import { Calendar, MapPin, Sparkles, Flame, ExternalLink, Tag, Bookmark, X, Check } from 'lucide-react';
import { motion, useMotionValue, useTransform, AnimatePresence, animate } from 'framer-motion';

const FALLBACK_IMAGE = "https://images.unsplash.com/photo-1492684223066-81342ee5ff30?auto=format&fit=crop&w=800&q=80";

export default function SwipeDeck({ events, mood, onSwipe }) {
  if (!events || events.length === 0) {
    return (
      <div className="h-96 rounded-3xl border-2 border-dashed border-parchment-200 flex flex-col items-center justify-center p-6 text-center bg-parchment-100/50">
        <Sparkles className="w-10 h-10 text-terracotta/40 mb-3" />
        <h3 className="font-serif text-lg font-bold text-parchment-900">You've explored all local vibes</h3>
        <p className="text-xs text-parchment-800 mt-1 max-w-xs">
          Adjust your mood sliders above or check back later as new events get ingested.
        </p>
      </div>
    );
  }

  const activeEvent = events[0];

  const calculateMatchScore = (eVector) => {
    if (!eVector) return 50;
    const e = eVector.energy ?? eVector.energy_vector ?? 0.5;
    const s = eVector.social ?? eVector.social_vector ?? 0.5;
    const n = eVector.novelty ?? eVector.novelty_vector ?? 0.5;

    const moodEnergy = mood?.energy ?? 0.5;
    const moodSocial = mood?.social ?? 0.5;
    const moodNovelty = mood?.novelty ?? 0.5;

    const dE = moodEnergy - e;
    const dS = moodSocial - s;
    const dN = moodNovelty - n;
    const distance = Math.sqrt(dE * dE + dS * dS + dN * dN);
    return Math.max(0, Math.min(100, Math.round((1 - distance / Math.sqrt(3)) * 100)));
  };

  const currentMatchScore = calculateMatchScore(activeEvent);

  const x = useMotionValue(0);
  const y = useMotionValue(0);

  const rotate = useTransform(x, [-200, 200], [-12, 12]);
  
  // Swipe Visual Overlay Opacities
  const swipeRejectOpacity = useTransform(x, [-80, -20], [1, 0]);
  const swipeVibeOpacity = useTransform(x, [20, 80], [0, 1]);
  const swipeMaybeOpacity = useTransform(y, [-80, -20], [1, 0]);

  const triggerSwipe = (action) => {
    if (action === 'not_my_scene') {
      animate(x, -300, { duration: 0.2 }).then(() => {
        onSwipe?.(action, activeEvent);
        x.set(0);
        y.set(0);
      });
    } else if (action === 'totally_vibe') {
      animate(x, 300, { duration: 0.2 }).then(() => {
        onSwipe?.(action, activeEvent);
        x.set(0);
        y.set(0);
      });
    } else if (action === 'maybe_later') {
      animate(y, -300, { duration: 0.2 }).then(() => {
        onSwipe?.(action, activeEvent);
        x.set(0);
        y.set(0);
      });
    }
  };

  const handleDragEnd = (e, info) => {
    const offsetX = info.offset.x;
    const offsetY = info.offset.y;

    if (offsetY < -80 && Math.abs(offsetY) > Math.abs(offsetX)) {
      triggerSwipe('maybe_later');
    } else if (offsetX > 80) {
      triggerSwipe('totally_vibe');
    } else if (offsetX < -80) {
      triggerSwipe('not_my_scene');
    } else {
      animate(x, 0, { type: 'spring', stiffness: 300, damping: 25 });
      animate(y, 0, { type: 'spring', stiffness: 300, damping: 25 });
    }
  };

  const parsedSchedule = (() => {
    let scheduleData = activeEvent?.schedule;
    if (!scheduleData) return [];

    if (typeof scheduleData === 'string') {
      try {
        scheduleData = JSON.parse(scheduleData);
        if (typeof scheduleData === 'string') {
          scheduleData = JSON.parse(scheduleData);
        }
      } catch (err) {
        return [];
      }
    }

    const formatDates = (rawDateStr) => {
      if (!rawDateStr) return 'Register';
      const dateOptions = { month: 'short', day: 'numeric', year: 'numeric' };
      const strVal = String(rawDateStr);

      const parseLocal = (dStr) => {
        const trimmed = dStr.trim();
        if (/^\d{4}-\d{2}-\d{2}$/.test(trimmed)) {
          return new Date(`${trimmed}T00:00:00`);
        }
        return new Date(trimmed);
      };

      if (strVal.includes(',')) {
        const dates = strVal
          .split(',')
          .map((d) => parseLocal(d))
          .filter((d) => !isNaN(d.getTime()));

        if (dates.length >= 2) {
          const start = dates[0].toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
          const end = dates[dates.length - 1].toLocaleDateString('en-US', dateOptions);
          return `${start} - ${end}`;
        } else if (dates.length === 1) {
          return dates[0].toLocaleDateString('en-US', dateOptions);
        }
      }

      const parsedDate = parseLocal(strVal);
      if (!isNaN(parsedDate.getTime())) {
        return parsedDate.toLocaleDateString('en-US', dateOptions);
      }

      return strVal;
    };

    if (Array.isArray(scheduleData)) {
      return scheduleData.map((item) => ({
        label: formatDates(item.start_time || item.date || item.time),
        registerUrl: item.register || item.url || item.external_url || activeEvent.external_url || '#',
      }));
    }

    if (typeof scheduleData === 'object' && scheduleData !== null) {
      if ('start_time' in scheduleData || 'register' in scheduleData || 'external_url' in scheduleData) {
        return [{
          label: formatDates(scheduleData.start_time),
          registerUrl: scheduleData.register || scheduleData.external_url || activeEvent.external_url || '#',
        }];
      }

      return Object.entries(scheduleData).map(([key, value]) => ({
        label: formatDates(key),
        registerUrl: typeof value === 'string' ? value : activeEvent.external_url || '#',
      }));
    }

    return [];
  })();

  const venue = activeEvent.venue_name || activeEvent.venue || 'Local Venue';
  const mapUrl = activeEvent.latitude && activeEvent.longitude 
    ? `https://www.google.com/maps/search/?api=1&query=${activeEvent.latitude},${activeEvent.longitude}`
    : `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(venue + ' ' + (activeEvent.address || 'Tacoma, WA'))}`;

  return (
    <div className="relative w-full max-w-md mx-auto flex flex-col items-center gap-4 my-auto">
      <AnimatePresence mode="wait">
        <motion.div
          key={activeEvent.id || activeEvent.title || 'active-card'}
          initial={{ scale: 0.95, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          exit={{ scale: 0.95, opacity: 0 }}
          transition={{ type: "spring", stiffness: 300, damping: 25 }}
          style={{ x, y, rotate }}
          drag
          dragSnapToOrigin
          dragElastic={0.6}
          onDragEnd={handleDragEnd}
          className="relative w-full min-h-[460px] max-h-[580px] bg-white border border-parchment-200 rounded-3xl shadow-xl flex flex-col justify-between cursor-grab active:cursor-grabbing select-none overflow-hidden touch-pan-y transform-gpu"
        >
          {/* SWIPE OVERLAY INDICATORS */}
          <motion.div
            style={{ opacity: swipeVibeOpacity }}
            className="absolute top-4 right-4 border-[3.5px] border-emerald-600 text-emerald-600 font-black tracking-widest px-4 py-1.5 rounded-2xl rotate-12 pointer-events-none z-30 bg-white/95 backdrop-blur-md shadow-2xl text-base uppercase flex items-center gap-2"
          >
            TOTALLY MY VIBE
          </motion.div>

          <motion.div
            style={{ opacity: swipeRejectOpacity }}
            className="absolute top-4 left-4 border-[3.5px] border-rose-600 text-rose-600 font-black tracking-widest px-4 py-1.5 rounded-2xl -rotate-12 pointer-events-none z-30 bg-white/95 backdrop-blur-md shadow-2xl text-base uppercase flex items-center gap-2"
          >
            NOT MY SCENE
          </motion.div>

          <motion.div
            style={{ opacity: swipeMaybeOpacity }}
            className="absolute top-4 left-1/2 -translate-x-1/2 border-[3.5px] border-amber-600 text-amber-600 font-black tracking-widest px-4 py-1.5 rounded-2xl pointer-events-none z-30 bg-white/95 backdrop-blur-md shadow-2xl text-base uppercase flex items-center gap-2"
          >
            <Bookmark className="w-5 h-5 fill-amber-600 stroke-[2.5]" />
            MAYBE LATER
          </motion.div>

          {/* CARD BODY CONTENT */}
          <div className="flex-1 flex flex-col justify-between overflow-y-auto touch-pan-y">
            <div>
              <div className="relative h-44 w-full bg-parchment-100 overflow-hidden shrink-0">
                <img
                  src={activeEvent.image_url || FALLBACK_IMAGE}
                  alt={activeEvent.title || 'Event image'}
                  onError={(e) => {
                    e.target.onerror = null;
                    e.target.src = FALLBACK_IMAGE;
                  }}
                  className="w-full h-full object-cover pointer-events-none"
                />
                <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-transparent to-black/20 pointer-events-none" />

                <div className="absolute top-4 left-4 right-4 flex justify-between items-center z-10 pointer-events-none">
                  <span className="text-[11px] font-semibold uppercase tracking-wider bg-black/50 backdrop-blur-md text-white px-2.5 py-1 rounded-full border border-white/20">
                    {activeEvent.category || 'Local Experience'}
                  </span>
                  <div className="flex items-center gap-1 bg-terracotta text-white px-2.5 py-1 rounded-full text-xs font-bold shadow-md">
                    <Flame className="w-3.5 h-3.5 fill-white" />
                    {currentMatchScore}% Match
                  </div>
                </div>
              </div>

              <div className="p-5 sm:p-6 space-y-2">
                <h2 className="font-serif text-xl font-bold text-parchment-900 leading-tight">
                  {activeEvent.title}
                </h2>
                <p className="text-xs text-parchment-800/80 leading-relaxed line-clamp-4">
                  {activeEvent.description}
                </p>
              </div>
            </div>

            <div className="px-5 pb-4 space-y-3">
              <div className="space-y-1.5 border-t border-parchment-100 pt-3 text-xs text-parchment-800">
                {mapUrl && (
                  <a 
                    href={mapUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    onPointerDown={(e) => e.stopPropagation()}
                    onClick={(e) => e.stopPropagation()}
                    className="flex items-center gap-2 text-terracotta font-medium hover:underline truncate"
                  >
                    <MapPin className="w-4 h-4 shrink-0 text-terracotta" />
                    <span className="truncate">{venue} {activeEvent.address ? `• ${activeEvent.address}` : ''}</span>
                  </a>
                )}

                {activeEvent.price_info && (
                  <div className="flex items-center gap-2 text-parchment-700">
                    <Tag className="w-4 h-4 text-terracotta shrink-0" />
                    <span>{activeEvent.price_info}</span>
                  </div>
                )}
              </div>

              <div className="mt-2 space-y-1.5">
                <div className="flex items-center gap-1.5 text-[11px] font-medium text-parchment-700">
                  <Calendar className="w-3.5 h-3.5 text-terracotta" />
                  <span>Select Date to Register:</span>
                </div>

                <div 
                  className="flex items-center gap-2 overflow-x-auto pb-1 pt-0.5 no-scrollbar touch-pan-x"
                  onPointerDown={(e) => e.stopPropagation()}
                >
                  {parsedSchedule.length > 0 ? (
                    parsedSchedule.map((item, idx) => (
                      <a
                        key={idx}
                        href={item.registerUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        onPointerDown={(e) => e.stopPropagation()}
                        onClick={(e) => e.stopPropagation()}
                        className="shrink-0 flex items-center gap-1.5 bg-parchment-100 hover:bg-terracotta hover:text-white text-parchment-900 border border-parchment-200 font-semibold py-1.5 px-3 rounded-xl text-xs shadow-sm transition-all"
                      >
                        <span>{item.label}</span>
                        <ExternalLink className="w-3 h-3 opacity-70" />
                      </a>
                    ))
                  ) : (
                    <span className="text-xs text-parchment-700 italic">No scheduled dates available</span>
                  )}
                </div>
              </div>
            </div>
          </div>
        </motion.div>
      </AnimatePresence>

      {/* DIRECT ACTION BUTTON CONTROLS */}
      <div className="flex items-center justify-center gap-6 pt-2 z-20">
        <button
          onClick={() => triggerSwipe('not_my_scene')}
          className="w-12 h-12 rounded-full bg-white border border-rose-200 text-rose-600 flex items-center justify-center shadow-md hover:bg-rose-50 active:scale-95 transition-all"
          aria-label="Pass"
        >
          <X className="w-6 h-6 stroke-[2.5]" />
        </button>

        <button
          onClick={() => triggerSwipe('maybe_later')}
          className="w-10 h-10 rounded-full bg-white border border-amber-200 text-amber-600 flex items-center justify-center shadow-md hover:bg-amber-50 active:scale-95 transition-all"
          aria-label="Save for later"
        >
          <Bookmark className="w-5 h-5 fill-amber-600" />
        </button>

        <button
          onClick={() => triggerSwipe('totally_vibe')}
          className="w-12 h-12 rounded-full bg-white border border-emerald-200 text-emerald-600 flex items-center justify-center shadow-md hover:bg-emerald-50 active:scale-95 transition-all"
          aria-label="Match vibe"
        >
          <Check className="w-6 h-6 stroke-[2.5]" />
        </button>
      </div>
    </div>
  );
}