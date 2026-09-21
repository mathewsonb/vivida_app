import React, { useState } from 'react';
import { motion, useMotionValue, useTransform, AnimatePresence } from 'framer-motion';
import { Calendar, MapPin, Sparkles, X, Heart, Flame } from 'lucide-react';

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

  // Calculate Euclidean Triaxial Distance Score
  const calculateMatchScore = (eVector) => {
    const dE = mood.energy - eVector.energy_vector;
    const dS = mood.social - eVector.social_vector;
    const dN = mood.novelty - eVector.novelty_vector;
    const distance = Math.sqrt(dE * dE + dS * dS + dN * dN);
    const score = Math.max(0, Math.min(100, Math.round((1 - distance / Math.sqrt(3)) * 100)));
    return score;
  };

  const currentMatchScore = calculateMatchScore(activeEvent);

  // Motion Values for Gestures
  const x = useMotionValue(0);
  const rotate = useTransform(x, [-200, 200], [-15, 15]);
  const opacity = useTransform(x, [-200, -150, 0, 150, 200], [0, 1, 1, 1, 0]);

  // Color transforms for swipe indicators
  const swipeRejectOpacity = useTransform(x, [-150, -20], [1, 0]);
  const swipeVibeOpacity = useTransform(x, [20, 150], [0, 1]);

  const handleDragEnd = (e, info) => {
    if (info.offset.x > 100) {
      onSwipe('totally_vibe', activeEvent);
    } else if (info.offset.x < -100) {
      onSwipe('not_my_scene', activeEvent);
    }
  };

  return (
    <div className="relative h-[440px] w-full flex items-center justify-center">
      <AnimatePresence>
        <motion.div
          key={activeEvent.id}
          style={{ x, rotate, opacity }}
          drag="x"
          dragConstraints={{ left: 0, right: 0 }}
          onDragEnd={handleDragEnd}
          className="absolute inset-0 bg-white border border-parchment-200 rounded-3xl shadow-xl p-6 flex flex-col justify-between cursor-grab active:cursor-grabbing select-none overflow-hidden"
        >
          {/* Swipe Visual Indicators Overlay */}
          <motion.div
            style={{ opacity: swipeVibeOpacity }}
            className="absolute top-6 right-6 border-2 border-emerald-600 text-emerald-600 font-bold px-3 py-1 rounded-xl rotate-12 pointer-events-none z-20 bg-white/80 backdrop-blur-sm"
          >
            TOTALLY MY VIBE
          </motion.div>
          <motion.div
            style={{ opacity: swipeRejectOpacity }}
            className="absolute top-6 left-6 border-2 border-rose-600 text-rose-600 font-bold px-3 py-1 rounded-xl -rotate-12 pointer-events-none z-20 bg-white/80 backdrop-blur-sm"
          >
            NOT MY SCENE
          </motion.div>

          {/* Top Row: Category Tag & Match Badge */}
          <div>
            <div className="flex justify-between items-start mb-3">
              <span className="text-[11px] font-semibold uppercase tracking-wider bg-parchment-100 text-parchment-800 px-2.5 py-1 rounded-full border border-parchment-200">
                {activeEvent.category || 'Local Experience'}
              </span>
              <div className="flex items-center gap-1 bg-terracotta/10 text-terracotta border border-terracotta/20 px-2.5 py-1 rounded-full text-xs font-bold">
                <Flame className="w-3.5 h-3.5 fill-terracotta" />
                {currentMatchScore}% Match
              </div>
            </div>

            {/* Event Title & Description */}
            <h2 className="font-serif text-2xl font-bold text-parchment-900 leading-tight mb-2">
              {activeEvent.title}
            </h2>
            <p className="text-xs text-parchment-800/80 line-clamp-4 leading-relaxed mb-4">
              {activeEvent.description}
            </p>
          </div>

          {/* Bottom Event Metadata */}
          <div className="space-y-2 border-t border-parchment-100 pt-4">
            <div className="flex items-center gap-2 text-xs text-parchment-800">
              <MapPin className="w-4 h-4 text-terracotta shrink-0" />
              <span className="font-medium truncate">{activeEvent.venue} — {activeEvent.address || 'Tacoma, WA'}</span>
            </div>
            <div className="flex items-center gap-2 text-xs text-parchment-800">
              <Calendar className="w-4 h-4 text-terracotta shrink-0" />
              <span>{new Date(activeEvent.start_time).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}</span>
            </div>
          </div>
        </motion.div>
      </AnimatePresence>
    </div>
  );
}