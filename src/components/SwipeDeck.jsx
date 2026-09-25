import React from 'react';
import { Calendar, MapPin, Sparkles, Flame, ExternalLink, Tag, Bookmark } from 'lucide-react';
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

  // Calculate Match Score
  const calculateMatchScore = (eVector) => {
    const e = eVector.energy ?? eVector.energy_vector ?? 0.5;
    const s = eVector.social ?? eVector.social_vector ?? 0.5;
    const n = eVector.novelty ?? eVector.novelty_vector ?? 0.5;

    const dE = mood.energy - e;
    const dS = mood.social - s;
    const dN = mood.novelty - n;
    const distance = Math.sqrt(dE * dE + dS * dS + dN * dN);
    return Math.max(0, Math.min(100, Math.round((1 - distance / Math.sqrt(3)) * 100)));
  };

  const currentMatchScore = calculateMatchScore(activeEvent);

  // Motion Values for 2D Gestures (X for left/right, Y for swipe up)
  const x = useMotionValue(0);
  const y = useMotionValue(0);

  const rotate = useTransform(x, [-200, 200], [-15, 15]);
  const opacity = useTransform(
    [x, y],
    ([latestX, latestY]) => {
      const distance = Math.sqrt(latestX * latestX + latestY * latestY);
      return Math.max(0, 1 - distance / 350);
    }
  );

  // Swipe Visual Overlay Opacities
  const swipeRejectOpacity = useTransform(x, [-150, -30], [1, 0]);
  const swipeVibeOpacity = useTransform(x, [30, 150], [0, 1]);
  const swipeMaybeOpacity = useTransform(y, [-150, -30], [1, 0]); // Triggers on dragging upward

  const handleDragEnd = (e, info) => {
    const offsetX = info.offset.x;
    const offsetY = info.offset.y;

    // 1. Swipe Up (Maybe Later)
    if (offsetY < -100 && Math.abs(offsetY) > Math.abs(offsetX)) {
      onSwipe('maybe_later', activeEvent);
      // Reset motion values for the incoming card
      x.set(0);
      y.set(0);
    } 
    // 2. Swipe Right (Totally My Vibe)
    else if (offsetX > 100) {
      onSwipe('totally_vibe', activeEvent);
      x.set(0);
      y.set(0);
    } 
    // 3. Swipe Left (Not My Scene)
    else if (offsetX < -100) {
      onSwipe('not_my_scene', activeEvent);
      x.set(0);
      y.set(0);
    } 
    // 4. Threshold NOT met -> Snap back smoothly to center
    else {
      animate(x, 0, { type: 'spring', stiffness: 500, damping: 30 });
      animate(y, 0, { type: 'spring', stiffness: 500, damping: 30 });
    }
  };

  const venue = activeEvent.venue_name || activeEvent.venue || 'Local Venue';
  const mapUrl = activeEvent.latitude && activeEvent.longitude 
    ? `https://www.google.com/maps/search/?api=1&query=${activeEvent.latitude},${activeEvent.longitude}`
    : `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(venue + ' ' + (activeEvent.address || 'Tacoma, WA'))}`;

  return (
    <div className="relative h-[560px] w-full flex items-center justify-center">
      <AnimatePresence>
          <motion.div
            key={activeEvent.id}
            initial={{ scale: 0.95, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.95, opacity: 0 }}
            transition={{ duration: 0.2 }}
            style={{ x, y, rotate, opacity }}
            drag
            dragConstraints={{ left: 0, right: 0, top: 0, bottom: 0 }}
            onDragEnd={handleDragEnd}
            className="absolute inset-0 bg-white border border-parchment-200 rounded-3xl shadow-xl flex flex-col justify-between cursor-grab active:cursor-grabbing select-none overflow-hidden"
          >
          {/* SWIPE OVERLAY INDICATORS */}
          {/* Right: Totally My Vibe */}
          <motion.div
            style={{ opacity: swipeVibeOpacity }}
            className="absolute top-6 right-6 border-2 border-emerald-600 text-emerald-600 font-bold px-3 py-1 rounded-xl rotate-12 pointer-events-none z-30 bg-white/90 backdrop-blur-sm shadow-md"
          >
            TOTALLY MY VIBE
          </motion.div>

          {/* Left: Not My Scene */}
          <motion.div
            style={{ opacity: swipeRejectOpacity }}
            className="absolute top-6 left-6 border-2 border-rose-600 text-rose-600 font-bold px-3 py-1 rounded-xl -rotate-12 pointer-events-none z-30 bg-white/90 backdrop-blur-sm shadow-md"
          >
            NOT MY SCENE
          </motion.div>

          {/* Up: Maybe Later */}
          <motion.div
            style={{ opacity: swipeMaybeOpacity }}
            className="absolute bottom-20 left-1/2 -translate-x-1/2 border-2 border-amber-600 text-amber-600 font-bold px-4 py-1.5 rounded-xl pointer-events-none z-30 bg-white/90 backdrop-blur-sm shadow-md flex items-center gap-1.5"
          >
            <Bookmark className="w-4 h-4 fill-amber-600" />
            MAYBE LATER
          </motion.div>

          {/* TOP SECTION: IMAGE HEADER & BADGES */}
          <div>
            <div className="relative h-44 w-full bg-parchment-100 overflow-hidden">
              <img
                src={activeEvent.image_url || FALLBACK_IMAGE}
                alt={activeEvent.title}
                onError={(e) => {
                  e.target.onerror = null;
                  e.target.src = FALLBACK_IMAGE;
                }}
                className="w-full h-full object-cover"
              />
              <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-transparent to-black/20" />

              <div className="absolute top-4 left-4 right-4 flex justify-between items-center z-10">
                <span className="text-[11px] font-semibold uppercase tracking-wider bg-black/50 backdrop-blur-md text-white px-2.5 py-1 rounded-full border border-white/20">
                  {activeEvent.category || 'Local Experience'}
                </span>
                <div className="flex items-center gap-1 bg-terracotta text-white px-2.5 py-1 rounded-full text-xs font-bold shadow-md">
                  <Flame className="w-3.5 h-3.5 fill-white" />
                  {currentMatchScore}% Match
                </div>
              </div>
            </div>

            {/* EVENT DETAILS */}
            <div className="p-5">
              <h2 className="font-serif text-xl font-bold text-parchment-900 leading-tight mb-2">
                {activeEvent.title}
              </h2>
              <p className="text-xs text-parchment-800/80 line-clamp-3 leading-relaxed mb-3">
                {activeEvent.description}
              </p>
            </div>
          </div>

          {/* BOTTOM METADATA & ACTIONS */}
          <div className="px-5 pb-5 space-y-3">
            <div className="space-y-1.5 border-t border-parchment-100 pt-3 text-xs text-parchment-800">
              <a 
                href={mapUrl}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => e.stopPropagation()}
                className="flex items-center gap-2 text-terracotta font-medium hover:underline truncate"
              >
                <MapPin className="w-4 h-4 shrink-0 text-terracotta" />
                <span className="truncate">{venue} {activeEvent.address ? `• ${activeEvent.address}` : ''}</span>
              </a>

              <div className="flex items-center gap-2">
                <Calendar className="w-4 h-4 text-terracotta shrink-0" />
                <span>
                  {(() => {
                    const options = { 
                      weekday: 'short', 
                      month: 'short', 
                      day: 'numeric' 
                    };

                    const formattedDates = (activeEvent.start_time || '')
                      .split(',')
                      .map(s => new Date(s.trim()))
                      .filter(d => !isNaN(d.getTime()))
                      .map(d => d.toLocaleDateString('en-US', options));

                    return formattedDates.join(' - ');
                  })()}
                </span>
              </div>

              {activeEvent.price_info && (
                <div className="flex items-center gap-2 text-parchment-700">
                  <Tag className="w-4 h-4 text-terracotta shrink-0" />
                  <span>{activeEvent.price_info}</span>
                </div>
              )}
            </div>

            {/* TICKET REDIRECT */}
            {activeEvent.external_url && (
              <a
                href={activeEvent.external_url}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => e.stopPropagation()}
                className="w-full flex items-center justify-center gap-1.5 bg-terracotta hover:bg-terracotta-600 text-white font-semibold py-2 px-4 rounded-xl text-xs shadow-sm transition-colors mt-2"
              >
                <span>Get Tickets / View Event</span>
                <ExternalLink className="w-3.5 h-3.5" />
              </a>
            )}
          </div>
        </motion.div>
      </AnimatePresence>
    </div>
  );
}