// src/components/SwipeDeck.jsx
import React, { useEffect } from 'react';
import { Calendar, MapPin, Sparkles, Flame, ExternalLink, Tag, Bookmark, RotateCcw } from 'lucide-react';
import { motion, useMotionValue, useTransform, AnimatePresence, animate } from 'framer-motion';

const FALLBACK_IMAGE = "https://images.unsplash.com/photo-1492684223066-81342ee5ff30?auto=format&fit=crop&w=800&q=80";

function sanitizeUrl(url) {
  if (!url || typeof url !== 'string') return '#';
  try {
    const parsed = new URL(url, window.location.origin);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
      return parsed.href;
    }
  } catch (e) {
    // Malformed URL fallback
  }
  return '#';
}

export default function SwipeDeck({ events, mood, onSwipe, onReset }) {
  if (!events || events.length === 0) {
    return (
      <div className="h-72 rounded-3xl border-2 border-dashed border-parchment-200 flex flex-col items-center justify-center p-6 text-center bg-parchment-100/50 space-y-3">
        <Sparkles className="w-9 h-9 text-terracotta/40 mb-1" />
        <h3 className="font-serif text-base font-bold text-parchment-900">
          You've explored all local vibes
        </h3>
        <p className="text-xs text-parchment-800 max-w-xs leading-relaxed">
          Adjust your mood sliders above, clear your history to explore again, or check back later as new events get ingested.
        </p>

        {onReset && (
          <button
            onClick={onReset}
            className="mt-2 inline-flex items-center gap-2 bg-terracotta text-white font-bold px-4 py-2 rounded-2xl hover:bg-terracotta-hover transition-transform active:scale-95 text-xs shadow-md"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            <span>Reset Deck & Clear History</span>
          </button>
        )}
      </div>
    );
  }

  const activeEvent = events[0];

  const currentMatchScore = (() => {
    if (typeof activeEvent.matchScore === 'number') {
      return Math.round(activeEvent.matchScore * 100);
    }
    const e = activeEvent.energy ?? activeEvent.energy_vector ?? 0.5;
    const s = activeEvent.social ?? activeEvent.social_vector ?? 0.5;
    const n = activeEvent.novelty ?? activeEvent.novelty_vector ?? 0.5;

    const moodEnergy = mood?.energy ?? 0.5;
    const moodSocial = mood?.social ?? 0.5;
    const moodNovelty = mood?.novelty ?? 0.5;

    const distSquared = (moodEnergy - e) ** 2 + (moodSocial - s) ** 2 + (moodNovelty - n) ** 2;
    const similarity = Math.exp(-distSquared / (2 * (0.5 ** 2)));
    return Math.round(similarity * 100);
  })();

  const getBadgeStyle = (score) => {
    if (score >= 85) return 'bg-emerald-600 text-white';
    if (score >= 65) return 'bg-amber-600 text-white';
    return 'bg-terracotta text-white';
  };

  const x = useMotionValue(0);
  const y = useMotionValue(0);

  // Automatically reset drag offsets whenever the active card ID changes
  useEffect(() => {
    x.set(0);
    y.set(0);
  }, [activeEvent?.id, x, y]);

  const rotate = useTransform(x, [-200, 200], [-12, 12]);

  // Badge animation transformations
  const rejectScale = useTransform(x, [0, -150], [1, 2]);
  const swipeRejectOpacity = useTransform(x, [0, -150], [0, 1]);
  const rejectBlur = useTransform(x, [0, -150], ["blur(10px)", "blur(0px)"]);

  const vibeScale = useTransform(x, [0, 150], [1, 2]);
  const swipeVibeOpacity = useTransform(x, [0, 150], [0, 1]);
  const vibeBlur = useTransform(x, [0, 150], ["blur(10px)", "blur(0px)"]);

  const maybeScale = useTransform(y, [0, -150], [1, 2]);
  const swipeMaybeOpacity = useTransform(y, [0, -150], [0, 1]);
  const maybeBlur = useTransform(y, [0, -150], ["blur(10px)", "blur(0px)"]);

  const triggerSwipe = (action) => {
    let targetX = 0;
    let targetY = 0;

    if (action === 'not_my_scene') targetX = -400;
    if (action === 'totally_vibe') targetX = 400;
    if (action === 'maybe_later') targetY = -400;

    const animX = targetX !== 0 ? animate(x, targetX, { duration: 0.18 }) : Promise.resolve();
    const animY = targetY !== 0 ? animate(y, targetY, { duration: 0.18 }) : Promise.resolve();

    Promise.all([animX, animY]).then(() => {
      x.set(0);
      y.set(0);
      onSwipe?.(action, activeEvent);
    });
  };

  const handleDragEnd = (e, info) => {
    const offsetX = info.offset.x;
    const offsetY = info.offset.y;

    if (offsetY < -50 && Math.abs(offsetY) > Math.abs(offsetX) * 0.6) {
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
        if (typeof scheduleData === 'string') scheduleData = JSON.parse(scheduleData);
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
        registerUrl: sanitizeUrl(item.register || item.url || item.external_url || activeEvent.external_url),
      }));
    }

    if (typeof scheduleData === 'object' && scheduleData !== null) {
      if ('start_time' in scheduleData || 'register' in scheduleData || 'external_url' in scheduleData) {
        return [{
          label: formatDates(scheduleData.start_time),
          registerUrl: sanitizeUrl(scheduleData.register || scheduleData.external_url || activeEvent.external_url),
        }];
      }

      return Object.entries(scheduleData).map(([key, value]) => ({
        label: formatDates(key),
        registerUrl: sanitizeUrl(typeof value === 'string' ? value : activeEvent.external_url),
      }));
    }

    return [];
  })();

  const venue = activeEvent.venue_name || activeEvent.venue || 'Local Venue';
  const mapUrl = activeEvent.latitude && activeEvent.longitude 
    ? sanitizeUrl(`https://www.google.com/maps/search/?api=1&query=${activeEvent.latitude},${activeEvent.longitude}`)
    : sanitizeUrl(`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(venue + ' ' + (activeEvent.address || 'Tacoma, WA'))}`);

  return (
    <div className="relative w-full max-w-md mx-auto my-auto pt-2">
      {/* TOTALLY MY VIBE BADGE */}
      <motion.div 
        style={{ 
          x: "-50%",
          y: "-50%",
          scale: vibeScale, 
          opacity: swipeVibeOpacity,
          filter: vibeBlur 
        }}
        className="absolute top-1/2 left-1/2 border-[3px] border-emerald-600 text-emerald-600 font-black tracking-widest px-4 py-1.5 rounded-2xl pointer-events-none z-30 bg-white/95 shadow-2xl text-xs uppercase flex items-center gap-2 whitespace-nowrap transform-gpu origin-center"
      >
        TOTALLY MY VIBE
      </motion.div>

      {/* NOT MY SCENE BADGE */}
      <motion.div 
        style={{ 
          x: "-50%",
          y: "-50%",
          scale: rejectScale, 
          opacity: swipeRejectOpacity,
          filter: rejectBlur 
        }}
        className="absolute top-1/2 left-1/2 border-[3px] border-rose-600 text-rose-600 font-black tracking-widest px-4 py-1.5 rounded-2xl pointer-events-none z-30 bg-white/95 shadow-2xl text-xs uppercase flex items-center gap-2 whitespace-nowrap transform-gpu origin-center"
      >
        NOT MY SCENE
      </motion.div>

      {/* MAYBE LATER BADGE */}
      <motion.div 
        style={{ 
          x: "-50%",
          y: "-50%",
          scale: maybeScale, 
          opacity: swipeMaybeOpacity,
          filter: maybeBlur 
        }}
        className="absolute top-1/2 left-1/2 border-[3px] border-amber-600 text-amber-600 font-black tracking-widest px-4 py-1.5 rounded-2xl pointer-events-none z-30 bg-white/95 shadow-2xl text-xs uppercase flex items-center gap-2 whitespace-nowrap transform-gpu origin-center"
      >
        <Bookmark className="w-4 h-4 fill-amber-600 stroke-[2.5]" />
        MAYBE LATER
      </motion.div>

      {/* INTERACTIVE SWIPE CARD WITH VIEWPORT HEIGHT CONSTRAINTS */}
      <AnimatePresence mode="popLayout">
        <motion.div
          key={activeEvent.id || activeEvent.title || 'active-card'}
          initial={{ scale: 0.95, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          exit={{ scale: 0.9, opacity: 0 }}
          transition={{ type: "spring", stiffness: 350, damping: 25 }}
          style={{ x, y, rotate }}
          drag
          dragDirectionLock
          dragSnapToOrigin
          dragElastic={0.6}
          onDragEnd={handleDragEnd}
          className="relative w-full h-[58dvh] min-h-[380px] max-h-[480px] bg-white border border-parchment-200 rounded-3xl shadow-xl flex flex-col justify-between cursor-grab active:cursor-grabbing select-none overflow-hidden touch-pan-y transform-gpu"
        >
          <div className="flex-1 flex flex-col justify-between overflow-hidden pointer-events-none">
            <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
              
              {/* IMAGE CONTAINER WITH BOTTOM-ALIGNED OVERLAYS */}
              <div className="relative h-32 sm:h-36 w-full bg-parchment-100 overflow-hidden shrink-0">
                <img
                  src={activeEvent.image_url || FALLBACK_IMAGE}
                  alt={activeEvent.title || 'Event image'}
                  referrerPolicy="no-referrer"
                  onError={(e) => {
                    e.target.onerror = null;
                    e.target.src = FALLBACK_IMAGE;
                  }}
                  className="w-full h-full object-cover pointer-events-none"
                />
                
                {/* BOTTOM GRADIENT FOR CONTRAST & LEGIBILITY */}
                <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/20 to-transparent pointer-events-none" />

                {/* BOTTOM BADGES (CATEGORY + MATCH SCORE) */}
                <div className="absolute bottom-2.5 left-2.5 right-2.5 flex justify-between items-center z-10 pointer-events-none">
                  <span className="text-[10px] font-semibold uppercase tracking-wider bg-black/60 backdrop-blur-md text-white px-2 py-0.5 rounded-full border border-white/20">
                    {activeEvent.category || 'Local Experience'}
                  </span>
                  <div className={`flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold shadow-md transition-colors ${getBadgeStyle(currentMatchScore)}`}>
                    <Flame className="w-3 h-3 fill-white" />
                    {currentMatchScore}% Match
                  </div>
                </div>
              </div>

              {/* CARD DETAILS WITH COMPACT SCROLLING */}
              <div className="p-3.5 space-y-1.5 overflow-y-auto no-scrollbar flex-1">
                <h2 className="font-serif text-base sm:text-lg font-bold text-parchment-900 leading-tight">
                  {activeEvent.title}
                </h2>
                <p className="text-[11px] sm:text-xs text-parchment-800/80 leading-relaxed line-clamp-3">
                  {activeEvent.description}
                </p>
              </div>
            </div>

            {/* CARD FOOTER AND REGISTRATION SCHEDULE */}
            <div className="px-3.5 pb-3 space-y-2 shrink-0 bg-white">
              <div className="space-y-1 border-t border-parchment-100 pt-2 text-[11px] text-parchment-800">
                {mapUrl && mapUrl !== '#' && (
                  <a 
                    href={mapUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    onPointerDown={(e) => e.stopPropagation()}
                    onClick={(e) => e.stopPropagation()}
                    className="pointer-events-auto flex items-center gap-1.5 text-terracotta font-medium hover:underline truncate"
                  >
                    <MapPin className="w-3.5 h-3.5 shrink-0 text-terracotta" />
                    <span className="truncate">{venue} {activeEvent.address ? `• ${activeEvent.address}` : ''}</span>
                  </a>
                )}

                {activeEvent.price_info && (
                  <div className="flex items-center gap-1.5 text-parchment-700">
                    <Tag className="w-3.5 h-3.5 text-terracotta shrink-0" />
                    <span>{activeEvent.price_info}</span>
                  </div>
                )}
              </div>

              <div className="space-y-1">
                <div className="flex items-center gap-1 text-[10px] font-medium text-parchment-700">
                  <Calendar className="w-3 h-3 text-terracotta" />
                  <span>Select Date to Register:</span>
                </div>

                <div 
                  className="pointer-events-auto flex items-center gap-1.5 overflow-x-auto pb-0.5 pt-0.5 no-scrollbar touch-pan-x"
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
                        className="shrink-0 flex items-center gap-1 bg-parchment-100 hover:bg-terracotta hover:text-white text-parchment-900 border border-parchment-200 font-semibold py-1 px-2.5 rounded-lg text-[11px] shadow-xs transition-all"
                      >
                        <span>{item.label}</span>
                        <ExternalLink className="w-2.5 h-2.5 opacity-70" />
                      </a>
                    ))
                  ) : (
                    <span className="text-[11px] text-parchment-700 italic">No scheduled dates available</span>
                  )}
                </div>
              </div>
            </div>
          </div>
        </motion.div>
      </AnimatePresence>
    </div>
  );
}