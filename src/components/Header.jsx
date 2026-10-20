// src/components/Header.jsx

import React, { useState, useEffect, useRef } from 'react';
import { 
  SlidersHorizontal, 
  History, 
  ChevronUp, 
  Flame, 
  Bookmark, 
  ShipWheel,
  RotateCcw
} from 'lucide-react';
import MoodSliders from './MoodSliders';

export default function Header({ 
  mood, 
  onChangeMood, 
  onOpenHistory, 
  onResetLocalHistory,
  vibeCount = 0, 
  savedCount = 0, 
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
      <div className="max-w-md mx-auto px-4 py-3 flex items-center justify-between">
        
        {/* Logo / Brand with Graffiti City Qualifier */}
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-xl bg-terracotta text-white flex items-center justify-center shadow-sm">
            <ShipWheel className="w-4 h-4" />
          </div>
          <div className="flex items-center gap-2">
            <span className="font-serif font-bold text-lg tracking-tight text-parchment-900">
              Vivida
            </span>

            <span 
              style={{ fontFamily: "'Permanent Marker', cursive" }}
              className="inline-block transform -rotate-3 text-terracotta text-sm tracking-wide leading-none select-none drop-shadow-2xs"
            >
              {city}
            </span>
          </div>
        </div>

        {/* Action Controls */}
        <div className="flex items-center gap-1.5">
          
          {/* Mood Slider Toggle */}
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setIsSlidersOpen((prev) => !prev);
            }}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold border transition-all ${
              isSlidersOpen
                ? 'bg-terracotta text-white border-terracotta shadow-sm'
                : 'bg-white text-parchment-800 border-parchment-200 hover:border-terracotta/50'
            }`}
            title="Adjust Mood Vibe"
          >
            <SlidersHorizontal className="w-3.5 h-3.5" />
            <span>Vibe</span>
          </button>

          {/* Top-Bar Reset Button with Labeled Tooltip */}
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              if (onResetLocalHistory) onResetLocalHistory();
            }}
            className="flex items-center gap-1 px-2.5 py-1.5 rounded-full bg-white border border-parchment-200 text-parchment-700 hover:text-rose-600 hover:border-rose-200 hover:bg-rose-50 transition-all text-xs font-semibold shadow-2xs"
            title="Reset local mood vectors and deck history"
            aria-label="Reset Local Vibe"
          >
            <RotateCcw className="w-3.5 h-3.5 text-rose-600" />
            <span className="text-[11px] font-bold text-parchment-800">Reset</span>
          </button>

          {/* Unified History Button */}
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              onOpenHistory();
            }}
            className="relative p-2 rounded-full bg-white border border-parchment-200 text-parchment-800 hover:bg-parchment-100 transition-colors flex items-center gap-1"
            title="Selection History"
          >
            <History className="w-4 h-4" />

            {totalCount > 0 && (
              <div className="flex items-center gap-0.5 ml-0.5">
                {vibeCount > 0 && (
                  <span className="flex items-center gap-0.5 bg-emerald-600 text-white text-[10px] font-bold px-1.5 py-0.2 rounded-full">
                    <Flame className="w-2.5 h-2.5 fill-current" />
                    {vibeCount}
                  </span>
                )}
                {savedCount > 0 && (
                  <span className="flex items-center gap-0.5 bg-amber-600 text-white text-[10px] font-bold px-1.5 py-0.2 rounded-full">
                    <Bookmark className="w-2.5 h-2.5 fill-current" />
                    {savedCount}
                  </span>
                )}
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