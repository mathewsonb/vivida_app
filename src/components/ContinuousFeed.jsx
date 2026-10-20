// src/components/ContinuousFeed.jsx
import React, { useState, useMemo } from 'react';
import { 
  Calendar, 
  MapPin, 
  ShipWheel, 
  Flame, 
  Tag, 
  ThumbsDown, 
  Heart, 
  RotateCcw, 
  ExternalLink, 
  X, 
  Search, 
  DollarSign, 
  Filter,
  ChevronRight
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

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

export default function ContinuousFeed({ events, mood, onSwipe, onReset }) {
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState('All');
  const [freeOnly, setFreeOnly] = useState(false);
  const [activeModalEvent, setActiveModalEvent] = useState(null);

  // Extract unique categories dynamically
  const categories = useMemo(() => {
    if (!events) return ['All'];
    const set = new Set();
    events.forEach((ev) => {
      if (ev.category) set.add(ev.category);
    });
    return ['All', ...Array.from(set).sort()];
  }, [events]);

  const getMatchScore = (event) => {
    if (typeof event.matchScore === 'number') {
      return Math.round(event.matchScore * 100);
    }
    const e = event.energy ?? event.energy_vector ?? 0.5;
    const s = event.social ?? event.social_vector ?? 0.5;
    const n = event.novelty ?? event.novelty_vector ?? 0.5;

    const moodEnergy = mood?.energy ?? 0.5;
    const moodSocial = mood?.social ?? 0.5;
    const moodNovelty = mood?.novelty ?? 0.5;

    const distSquared = (moodEnergy - e) ** 2 + (moodSocial - s) ** 2 + (moodNovelty - n) ** 2;
    const similarity = Math.exp(-distSquared / (2 * (0.5 ** 2)));
    return Math.round(similarity * 100);
  };

  const getBadgeStyle = (score) => {
    if (score >= 85) return 'bg-emerald-600 text-white';
    if (score >= 65) return 'bg-amber-600 text-white';
    return 'bg-terracotta text-white';
  };

  const getScheduleList = (event) => {
    let raw = event?.schedule;
    if (!raw) return [];
    if (typeof raw === 'string') {
      try {
        raw = JSON.parse(raw);
        if (typeof raw === 'string') raw = JSON.parse(raw);
      } catch (err) {
        return [];
      }
    }
    if (Array.isArray(raw)) {
      return raw.map((i) => ({
        dateStr: i.start_time || i.date || i.time || '',
        url: i.register || i.url || i.external_url || event.external_url || '#',
      }));
    }
    if (typeof raw === 'object' && raw !== null) {
      if ('start_time' in raw || 'register' in raw) {
        return [{ dateStr: raw.start_time || '', url: raw.register || event.external_url || '#' }];
      }
      return Object.entries(raw).map(([key, value]) => ({
        dateStr: key,
        url: typeof value === 'string' ? value : event.external_url || '#',
      }));
    }
    return [];
  };

  // Filter and group events by day
  const chronologicalGroups = useMemo(() => {
    if (!events) return [];

    const filtered = events.filter((ev) => {
      const q = searchQuery.toLowerCase().trim();
      if (q) {
        const titleMatch = ev.title?.toLowerCase().includes(q);
        const descMatch = ev.description?.toLowerCase().includes(q);
        const venueMatch = (ev.venue_name || ev.venue || '').toLowerCase().includes(q);
        if (!titleMatch && !descMatch && !venueMatch) return false;
      }

      if (selectedCategory !== 'All' && ev.category !== selectedCategory) {
        return false;
      }

      if (freeOnly) {
        const isFree = ev.is_free || (ev.price_info && ev.price_info.toLowerCase().includes('free'));
        if (!isFree) return false;
      }

      return true;
    });

    const map = {};
    const undated = [];

    filtered.forEach((event) => {
      const schedules = getScheduleList(event);
      let primaryDateKey = null;

      if (schedules.length > 0) {
        for (const sched of schedules) {
          const rawDate = String(sched.dateStr).trim();
          const isoMatches = rawDate.match(/\d{4}-\d{2}-\d{2}/g);
          if (isoMatches && isoMatches.length > 0) {
            primaryDateKey = isoMatches[0];
            break;
          } else if (rawDate) {
            const parsed = new Date(rawDate);
            if (!isNaN(parsed.getTime())) {
              const y = parsed.getFullYear();
              const m = String(parsed.getMonth() + 1).padStart(2, '0');
              const d = String(parsed.getDate()).padStart(2, '0');
              primaryDateKey = `${y}-${m}-${d}`;
              break;
            }
          }
        }
      }

      if (primaryDateKey) {
        if (!map[primaryDateKey]) map[primaryDateKey] = [];
        map[primaryDateKey].push({ event, scheduleUrl: schedules[0]?.url || event.external_url });
      } else {
        undated.push({ event, scheduleUrl: event.external_url });
      }
    });

    const now = new Date();
    const todayStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;

    const sortedDates = Object.keys(map).sort();

    const result = sortedDates.map((dateKey) => {
      const [y, m, d] = dateKey.split('-').map(Number);
      const dateObj = new Date(y, m - 1, d);
      return {
        dateKey,
        dateObj,
        isToday: dateKey === todayStr,
        items: map[dateKey],
      };
    });

    if (undated.length > 0) {
      result.push({
        dateKey: 'upcoming',
        dateObj: null,
        isToday: false,
        items: undated,
      });
    }

    return result;
  }, [events, searchQuery, selectedCategory, freeOnly]);

  return (
    <div className="w-full h-full flex flex-col overflow-hidden relative">
      {/* SEARCH AND FILTER BAR */}
      <div className="space-y-2 pb-3 border-b border-parchment-200 shrink-0">
        <div className="relative w-full">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-parchment-400" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search events, venues, descriptions..."
            className="w-full bg-white border border-parchment-200 rounded-xl pl-9 pr-8 py-2 text-xs text-parchment-900 placeholder:text-parchment-400 focus:outline-none focus:border-terracotta transition-colors shadow-2xs"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery('')}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-parchment-400 hover:text-parchment-700"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>

        <div className="flex items-center gap-1.5 overflow-x-auto no-scrollbar py-0.5">
          {categories.map((cat) => (
            <button
              key={cat}
              onClick={() => setSelectedCategory(cat)}
              className={`shrink-0 px-2.5 py-1 rounded-lg text-[11px] font-bold transition-all ${
                selectedCategory === cat
                  ? 'bg-terracotta text-white shadow-2xs'
                  : 'bg-white text-parchment-700 border border-parchment-200 hover:bg-parchment-100'
              }`}
            >
              {cat}
            </button>
          ))}
        </div>

        <div className="flex items-center justify-between text-xs pt-0.5">
          <button
            onClick={() => setFreeOnly((prev) => !prev)}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-semibold border transition-all ${
              freeOnly
                ? 'bg-emerald-600 text-white border-emerald-600'
                : 'bg-white text-parchment-700 border-parchment-200 hover:bg-parchment-100'
            }`}
          >
            <DollarSign className="w-3 h-3" />
            <span>Free Only</span>
          </button>
        </div>
      </div>

      {/* CHRONOLOGICAL SCROLLABLE FRAMES */}
      <div className="flex-1 overflow-y-auto no-scrollbar py-3 space-y-5 min-h-0">
        {chronologicalGroups.length === 0 ? (
          <div className="py-12 text-center space-y-2 bg-parchment-100/40 rounded-2xl border border-dashed border-parchment-200 p-4">
            <Filter className="w-8 h-8 text-parchment-400 mx-auto" />
            <h4 className="font-serif font-bold text-sm text-parchment-900">No matching events found</h4>
            <p className="text-xs text-parchment-600 max-w-xs mx-auto">
              Try adjusting your search query or clearing selected filters.
            </p>
            {onReset && (
              <button
                onClick={onReset}
                className="mt-2 inline-flex items-center gap-1.5 bg-terracotta text-white font-bold px-3 py-1.5 rounded-xl text-xs"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                <span>Reset History</span>
              </button>
            )}
          </div>
        ) : (
          chronologicalGroups.map((group) => {
            const formattedDateLabel = group.dateObj
              ? group.dateObj.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })
              : 'Upcoming Experiences';

            return (
              /* DAY CONTAINER FRAME */
              <div 
                key={group.dateKey} 
                className="bg-parchment-100/70 border border-parchment-200/90 rounded-3xl p-3 shadow-2xs space-y-3"
              >
                {/* FRAME HEADER */}
                <div className="flex items-center justify-between pb-2 border-b border-parchment-200/80 px-1">
                  <div className="flex items-center gap-2">
                    <div className="w-7 h-7 rounded-xl bg-terracotta/10 text-terracotta flex items-center justify-center shrink-0">
                      <Calendar className="w-4 h-4" />
                    </div>
                    <div>
                      <span className="text-xs font-serif font-bold text-parchment-900 uppercase tracking-wide block">
                        {formattedDateLabel}
                      </span>
                    </div>
                    {group.isToday && (
                      <span className="bg-amber-100 text-amber-800 text-[10px] font-extrabold px-2 py-0.5 rounded-full border border-amber-300">
                        Today
                      </span>
                    )}
                  </div>
                  <span className="text-[10px] bg-white border border-parchment-200 text-parchment-700 font-bold px-2 py-0.5 rounded-full shadow-2xs">
                    {group.items.length} {group.items.length === 1 ? 'Event' : 'Events'}
                  </span>
                </div>

                {/* EVENTS INSIDE THIS DAY'S FRAME */}
                <div className="space-y-2.5">
                  <AnimatePresence mode="popLayout">
                    {group.items.map(({ event, scheduleUrl }) => {
                      const matchScore = getMatchScore(event);
                      const venue = event.venue_name || event.venue || 'Local Venue';

                      return (
                        <motion.div
                          key={event.id || event.title}
                          layout
                          initial={{ opacity: 0, y: 10 }}
                          animate={{ opacity: 1, y: 0 }}
                          exit={{ opacity: 0, scale: 0.95, height: 0, marginBottom: 0 }}
                          transition={{ duration: 0.2 }}
                          className="bg-white border border-parchment-200/80 rounded-2xl p-2.5 shadow-2xs hover:border-terracotta/60 transition-colors cursor-pointer space-y-2"
                          onClick={() => setActiveModalEvent({ event, scheduleUrl, matchScore })}
                        >
                          <div className="flex gap-2.5 items-start">
                            <div className="relative w-20 h-20 shrink-0 rounded-xl overflow-hidden bg-parchment-100 border border-parchment-100">
                              <img
                                src={event.image_url || FALLBACK_IMAGE}
                                alt={event.title || 'Event image'}
                                referrerPolicy="no-referrer"
                                onError={(e) => {
                                  e.target.onerror = null;
                                  e.target.src = FALLBACK_IMAGE;
                                }}
                                className="w-full h-full object-cover"
                              />
                              <div className={`absolute bottom-1 left-1 px-1.5 py-0.5 rounded-md text-[9px] font-bold shadow-2xs ${getBadgeStyle(matchScore)}`}>
                                {matchScore}%
                              </div>
                            </div>

                            <div className="flex-1 min-w-0 space-y-1">
                              <span className="text-[9px] font-bold uppercase tracking-wider text-parchment-500 truncate block">
                                {event.category || 'Local Experience'}
                              </span>

                              <h4 className="font-serif font-bold text-xs text-parchment-900 leading-tight line-clamp-2">
                                {event.title}
                              </h4>

                              <p className="text-[11px] text-parchment-600 flex items-center gap-1 truncate">
                                <MapPin className="w-3 h-3 shrink-0 text-terracotta" />
                                <span className="truncate">{venue}</span>
                              </p>

                              {event.price_info && (
                                <p className="text-[10px] text-parchment-500 flex items-center gap-1 truncate">
                                  <Tag className="w-3 h-3 shrink-0 text-terracotta" />
                                  <span className="truncate">{event.price_info}</span>
                                </p>
                              )}
                            </div>
                          </div>

                          <div className="pt-1 flex items-center justify-between gap-2 border-t border-parchment-100/80">
                            <span className="inline-flex items-center gap-1 text-[10px] font-bold text-terracotta hover:underline">
                              <span>Details</span>
                              <ChevronRight className="w-3 h-3" />
                            </span>

                            <div 
                              className="flex items-center gap-1.5 ml-auto"
                              onClick={(e) => e.stopPropagation()}
                            >
                              <button
                                onClick={() => onSwipe?.('not_my_scene', event)}
                                className="p-1 rounded-lg border border-rose-200 text-rose-700 hover:bg-rose-50 transition-colors"
                                title="Pass"
                              >
                                <ThumbsDown className="w-3 h-3" />
                              </button>

                              <button
                                onClick={() => onSwipe?.('maybe_later', event)}
                                className="p-1 rounded-lg border border-amber-200 text-amber-700 hover:bg-amber-50 transition-colors"
                                title="Maybe"
                              >
                                <Heart className="w-3 h-3" />
                              </button>

                              <button
                                onClick={() => onSwipe?.('totally_vibe', event)}
                                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-lg bg-terracotta text-white font-bold text-[10px] hover:bg-terracotta-hover transition-colors shadow-2xs"
                              >
                                <Flame className="w-2.5 h-2.5 fill-white" />
                                <span>Vibe</span>
                              </button>
                            </div>
                          </div>
                        </motion.div>
                      );
                    })}
                  </AnimatePresence>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* POP-OUT DETAIL MODAL OVERLAY */}
      {activeModalEvent && (() => {
        const ev = activeModalEvent.event || {};
        const venue = ev.venue_name || ev.venue || 'Local Venue';
        const mapUrl = ev.latitude && ev.longitude
          ? `https://www.google.com/maps/search/?api=1&query=${ev.latitude},${ev.longitude}`
          : `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(venue + ' ' + (ev.address || 'Tacoma, WA'))}`;

        return (
          <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4 animate-in fade-in duration-200">
            <div className="bg-white rounded-3xl max-w-lg w-full max-h-[90dvh] overflow-y-auto border border-parchment-200 shadow-2xl flex flex-col relative animate-in zoom-in-95 duration-200">
              <button
                onClick={() => setActiveModalEvent(null)}
                className="absolute top-3 right-3 z-20 bg-white/80 backdrop-blur-md p-2 rounded-full text-parchment-700 hover:text-terracotta hover:bg-white shadow-md transition-colors"
                aria-label="Close modal"
              >
                <X className="w-5 h-5" />
              </button>

              <div className="relative h-56 w-full bg-parchment-100 shrink-0">
                <img
                  src={ev.image_url || FALLBACK_IMAGE}
                  alt={ev.title || 'Event'}
                  className="w-full h-full object-cover"
                  onError={(e) => { e.target.onerror = null; e.target.src = FALLBACK_IMAGE; }}
                />
                <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-transparent to-transparent" />
                <span className="absolute bottom-3 left-4 text-xs font-bold uppercase tracking-wider bg-black/50 text-white px-2.5 py-1 rounded-full border border-white/20 backdrop-blur-md">
                  {ev.category || 'Local Experience'}
                </span>
                <div className={`absolute bottom-3 right-4 px-2.5 py-1 rounded-full text-xs font-bold shadow-md ${getBadgeStyle(activeModalEvent.matchScore)}`}>
                  {activeModalEvent.matchScore}% Match
                </div>
              </div>

              <div className="p-6 space-y-4 flex-1">
                <div>
                  <h3 className="font-serif font-bold text-xl text-parchment-900 leading-tight">
                    {ev.title}
                  </h3>
                  <a
                    href={mapUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 text-xs text-terracotta font-medium hover:underline mt-1"
                  >
                    <MapPin className="w-3.5 h-3.5 shrink-0" />
                    <span>{venue} {ev.address ? `• ${ev.address}` : ''}</span>
                  </a>
                </div>

                <p className="text-xs sm:text-sm text-parchment-800 leading-relaxed bg-parchment-50 p-3.5 rounded-2xl border border-parchment-100">
                  {ev.description || 'No extended description available for this event.'}
                </p>

                <div className="flex flex-wrap gap-2 text-xs">
                  {ev.price_info && (
                    <span className="bg-emerald-100 text-emerald-800 font-bold px-2.5 py-1 rounded-full flex items-center gap-1">
                      <Tag className="w-3 h-3" /> {ev.price_info}
                    </span>
                  )}
                  {ev.age_restriction && (
                    <span className="bg-parchment-100 text-parchment-800 font-semibold px-2.5 py-1 rounded-full">
                      {ev.age_restriction}
                    </span>
                  )}
                  {ev.indoor_outdoor && (
                    <span className="bg-parchment-100 text-parchment-800 font-semibold px-2.5 py-1 rounded-full">
                      {ev.indoor_outdoor}
                    </span>
                  )}
                </div>

                <div className="pt-2 flex items-center justify-between gap-3 border-t border-parchment-100">
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => {
                        onSwipe?.('not_my_scene', ev);
                        setActiveModalEvent(null);
                      }}
                      className="p-2.5 rounded-xl border border-rose-200 text-rose-700 hover:bg-rose-50 transition-colors"
                      title="Pass"
                    >
                      <ThumbsDown className="w-4 h-4" />
                    </button>

                    <button
                      onClick={() => {
                        onSwipe?.('maybe_later', ev);
                        setActiveModalEvent(null);
                      }}
                      className="p-2.5 rounded-xl border border-amber-200 text-amber-700 hover:bg-amber-50 transition-colors"
                      title="Maybe"
                    >
                      <Heart className="w-4 h-4" />
                    </button>

                    <button
                      onClick={() => {
                        onSwipe?.('totally_vibe', ev);
                        setActiveModalEvent(null);
                      }}
                      className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl bg-terracotta text-white font-bold text-xs hover:bg-terracotta-hover transition-colors shadow-xs"
                    >
                      <Flame className="w-4 h-4 fill-white" />
                      <span>Vibe</span>
                    </button>
                  </div>

                  <a
                    href={sanitizeUrl(activeModalEvent.scheduleUrl || ev.external_url)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 bg-parchment-900 text-white font-bold px-4 py-2.5 rounded-xl text-xs hover:bg-black transition-colors"
                  >
                    <span>Get Details</span>
                    <ExternalLink className="w-3.5 h-3.5" />
                  </a>
                </div>
              </div>
            </div>
          </div>
        );
      })()}
    </div>
  );
}