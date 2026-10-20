// src/components/Header.jsx

import React, { useState, useEffect, useRef } from 'react';
import { 
  SlidersHorizontal, 
  History, 
  ChevronUp, 
  Flame, 
  Bookmark, 
  ShipWheel,
  RotateCcw,
  ShieldCheck
} from 'lucide-react';
import MoodSliders from './MoodSliders';

export default function Header({ 
  mood, 
  onChangeMood, 
  onOpenHistory, 
  onResetLocalHistory,
  vibeCount = 0, 
  savedCount = 0, 
  hasUserHash = false,
  city = "Tacoma"
}) {
  const [isSlidersOpen, setIsSlidersOpen] = useState(false);
  const collapseTimeoutRef = useRef(null);

  useEffect(() => {
    const fontId = 'permanent-marker-font';
    if (!document.getElementById(fontId)) {
      const link = document.createElement('link');
      link.id = fontId;
      link.rel = 'stylesheet';
      link.href = 'https://fonts.googleapis.com/css2?family=Permanent+Marker&display=swap';
      document.head.appendChild(link);
    }
  }, []);

  const handleMoodChange = (newMood) => {
    onChangeMood(newMood);

    if (collapseTimeoutRef.current) clearTimeout(collapseTimeoutRef.current);
    collapseTimeoutRef.current = setTimeout(() => {
      setIsSlidersOpen(false);
    }, 2500);
  };

  useEffect(() => {
    return () => {
      if (collapseTimeoutRef.current) clearTimeout(collapseTimeoutRef.current);
    };
  }, []);

  const totalCount = vibeCount + savedCount;

  return (
    <header className="sticky top-0 z-40 bg-parchment-50/95 backdrop-blur-md border-b border-parchment-200 shadow-sm">
      <div className="max-w-md mx-auto px-4 py-2.5 flex flex-col gap-2.5">
        
        {/* TIER 1: TOPMOST BRANDING HEADER */}
        <div className="flex items-center justify-between pt-0.5">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-xl bg-terracotta text-white flex items-center justify-center shadow-xs shrink-0">
              <ShipWheel className="w-4 h-4" />
            </div>
            <div className="flex items-center gap-2">
              <span className="font-serif font-bold text-xl tracking-tight text-parchment-900">
                Vivida
              </span>

              {/* Permanent Marker Tag */}
              <span 
                style={{ fontFamily: "'Permanent Marker', cursive" }}
                className="inline-block transform -rotate-3 text-terracotta text-base tracking-wide leading-none select-none drop-shadow-2xs"
              >
                {city}
              </span>
            </div>
          </div>

          {/* Cloud Sync Status Indicator */}
          {hasUserHash && (
            <div 
              className="flex items-center gap-1 text-[10px] text-emerald-700 bg-emerald-100/80 px-2 py-0.5 rounded-full font-semibold border border-emerald-200"
              title="Cloud Sync Active (Zero-PII Hashed)"
            >
              <ShieldCheck className="w-3 h-3 text-emerald-600" />
              <span>Synced</span>
            </div>
          )}
        </div>

        {/* TIER 2: ACTION CONTROLS ROW */}
        <div className="flex items-center justify-center gap-2 pt-0.5">
          
          {/* Mood Slider Toggle */}
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setIsSlidersOpen((prev) => !prev);
            }}
            className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-xs font-bold border transition-all shadow-2xs ${
              isSlidersOpen
                ? 'bg-terracotta text-white border-terracotta shadow-xs'
                : 'bg-white text-parchment-800 border-parchment-200 hover:border-terracotta/50'
            }`}
            title="Adjust Mood Vibe"
          >
            <SlidersHorizontal className="w-3.5 h-3.5" />
            <span>Vibe</span>
          </button>

          {/* Reset Button */}
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              if (onResetLocalHistory) onResetLocalHistory();
            }}
            className="flex items-center gap-1 px-3 py-1.5 rounded-full bg-white border border-parchment-200 text-parchment-800 hover:text-rose-600 hover:border-rose-200 hover:bg-rose-50 transition-all text-xs font-bold shadow-2xs"
            title="Reset local mood vectors and deck history"
            aria-label="Reset Local Vibe"
          >
            <RotateCcw className="w-3.5 h-3.5 text-rose-600" />
            <span>Reset</span>
          </button>

          {/* Unified History Button */}
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              onOpenHistory();
            }}
            className="relative px-3.5 py-1.5 rounded-full bg-white border border-parchment-200 text-parchment-800 hover:bg-parchment-100 transition-colors flex items-center gap-1.5 text-xs font-bold shadow-2xs"
            title="Selection History"
          >
            <History className="w-3.5 h-3.5 text-terracotta" />
            <span>History</span>

            {totalCount > 0 && (
              <div className="flex items-center gap-0.5 ml-0.5">
                <span className="flex items-center gap-0.5 bg-emerald-600 text-white text-[9px] font-bold px-1.5 py-0.2 rounded-full">
                  <Flame className="w-2.5 h-2.5 fill-current" />
                  {totalCount}
                </span>
              </div>
            )}
          </button>
        </div>
      </div>

      {/* Collapsible Drawer Area */}
      {isSlidersOpen && (
        <div className="border-t border-parchment-200/60 bg-parchment-100/90 px-4 py-3 shadow-inner">
          <div className="max-w-md mx-auto">
            <MoodSliders mood={mood} onChange={handleMoodChange} />
            <button
              type="button"
              onClick={() => setIsSlidersOpen(false)}
              className="mt-2 w-full flex items-center justify-center gap-1 text-[11px] font-medium text-parchment-700 hover:text-terracotta pt-1"
            >
              <span>Collapse controls</span>
              <ChevronUp className="w-3 h-3" />
            </button>
          </div>
        </div>
      )}
    </header>
  );
}