// src/components/Header.jsx

import React, { useState, useEffect, useRef } from 'react';
import { 
  SlidersHorizontal, 
  History, 
  ChevronUp, 
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
  hasUserHash = false,
  city = "Tacoma"
}) {
  const [isSlidersOpen, setIsSlidersOpen] = useState(false);
  const collapseTimeoutRef = useRef(null);

  // Animation lifecycle: 'floating' -> 'collapsing' -> 'docked'
  const [animState, setAnimState] = useState('floating');

  useEffect(() => {
    // 1. Ensure Permanent Marker font is loaded
    const fontId = 'permanent-marker-font';
    if (!document.getElementById(fontId)) {
      const link = document.createElement('link');
      link.id = fontId;
      link.rel = 'stylesheet';
      link.href = 'https://fonts.googleapis.com/css2?family=Permanent+Marker&display=swap';
      document.head.appendChild(link);
    }

    // 2. Extended Display Sequence:
    // Display hero banner for 3.0s, then run smooth morph/collapse transition over 1.2s
    const timer1 = setTimeout(() => {
      setAnimState('collapsing');
    }, 3000);

    const timer2 = setTimeout(() => {
      setAnimState('docked');
    }, 4200); // 3000ms display + 1200ms smooth path transition

    return () => {
      clearTimeout(timer1);
      clearTimeout(timer2);
    };
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

  return (
    <>
      {/* FLOATING OVERLAY: Anchored at top of card with borderless blurred backdrop */}
      {animState !== 'docked' && (
        <div 
          className={`fixed inset-0 z-50 pointer-events-none flex items-center justify-center transition-all duration-[2000ms] ease-in-out ${
            animState === 'collapsing' 
              ? 'opacity-0 scale-50 -translate-y-48 -translate-x-28' 
              : 'opacity-100 scale-100 translate-y-12 translate-x-0'
          }`}
        >
          {/* Borderless Blurred Container */}
          <div className="bg-parchment-50/80 backdrop-blur-md px-6 py-3 rounded-full shadow-lg flex items-center gap-3">
            <ShipWheel className="w-9 h-9 text-terracotta shrink-0 drop-shadow-xs" />
            <div className="flex items-center gap-2">
              
              {/* Morphing "Viva la vida" text wrapper */}
              <div className="relative flex items-center">
                <span 
                  className={`font-serif font-black text-3xl sm:text-4xl tracking-tight text-parchment-900 transition-all duration-[2500ms] ease-in-out ${
                    animState === 'collapsing' ? 'opacity-0 scale-90' : 'opacity-100 scale-100'
                  }`}
                >
                  Viva la Vida!
                </span>
                
                {/* Embedded "Vivida" target text */}
                <span 
                  className={`font-serif font-bold text-3xl sm:text-4xl tracking-tight text-parchment-900 absolute left-0 transition-all duration-[2500ms] ease-in-out ${
                    animState === 'collapsing' ? 'opacity-100 scale-100' : 'opacity-0 scale-90'
                  }`}
                >
                  Vivida
                </span>
              </div>

              {/* City Tag */}
              {/* <span 
                style={{ fontFamily: "'Permanent Marker', cursive" }}
                className="text-terracotta text-3xl sm:text-4xl tracking-wide leading-none transform -rotate-3 select-none"
              >
                {city}
              </span> */}
            </div>
          </div>
        </div>
      )}

      {/* HEADER BAR */}
      <header className="sticky top-0 z-40 bg-parchment-50/95 backdrop-blur-md border-b border-parchment-200 shadow-sm">
        <div className="max-w-md mx-auto px-4 py-2.5 flex flex-col gap-2.5">
          
          {/* TIER 1: TOPMOST BRANDING HEADER */}
          <div className="flex items-center justify-between pt-0.5">
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 rounded-xl bg-terracotta text-white flex items-center justify-center shadow-xs shrink-0">
                <ShipWheel className="w-4 h-4" />
              </div>

              {/* DOCKED HEADER LOGO (Fades in softly as banner morph completes) */}
              <div className={`flex items-center gap-2 transition-all duration-1000 ease-out ${
                animState === 'floating' ? 'opacity-0 scale-95' : 'opacity-100 scale-100'
              }`}>
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
    </>
  );
}