import React from 'react';
import { Zap, Users, Compass } from 'lucide-react';

export default function MoodSliders({ mood, onChange }) {
  const handleSliderChange = (key, value) => {
    onChange({
      ...mood,
      [key]: parseFloat(value)
    });
  };

  return (
    <div 
      className="space-y-3 py-1"
      onPointerDown={(e) => e.stopPropagation()}
    >
      {/* Energy Slider */}
      <div className="space-y-1">
        <div className="flex justify-between text-xs text-parchment-800 font-medium">
          <span className="flex items-center gap-1">
            <Zap className="w-3.5 h-3.5 text-amber-600" /> Energy
          </span>
          <span className="text-parchment-800/70 text-[11px]">
            {mood.energy < 0.4 ? 'Chill & Mellow' : mood.energy > 0.7 ? 'High Energy' : 'Balanced'}
          </span>
        </div>
        <input
          type="range"
          min="0"
          max="1"
          step="0.05"
          value={mood.energy}
          onChange={(e) => handleSliderChange('energy', e.target.value)}
          className="w-full h-1.5 bg-parchment-200/80 rounded-lg appearance-none cursor-pointer accent-terracotta touch-none"
        />
      </div>

      {/* Social Slider */}
      <div className="space-y-1">
        <div className="flex justify-between text-xs text-parchment-800 font-medium">
          <span className="flex items-center gap-1">
            <Users className="w-3.5 h-3.5 text-emerald-600" /> Social
          </span>
          <span className="text-parchment-800/70 text-[11px]">
            {mood.social < 0.4 ? 'Intimate / Solo' : mood.social > 0.7 ? 'Party / Crowd' : 'Small Group'}
          </span>
        </div>
        <input
          type="range"
          min="0"
          max="1"
          step="0.05"
          value={mood.social}
          onChange={(e) => handleSliderChange('social', e.target.value)}
          className="w-full h-1.5 bg-parchment-200/80 rounded-lg appearance-none cursor-pointer accent-terracotta touch-none"
        />
      </div>

      {/* Novelty Slider */}
      <div className="space-y-1">
        <div className="flex justify-between text-xs text-parchment-800 font-medium">
          <span className="flex items-center gap-1">
            <Compass className="w-3.5 h-3.5 text-indigo-600" /> Novelty
          </span>
          <span className="text-parchment-800/70 text-[11px]">
            {mood.novelty < 0.4 ? 'Classic / Familiar' : mood.novelty > 0.7 ? 'Experimental' : 'Fresh'}
          </span>
        </div>
        <input
          type="range"
          min="0"
          max="1"
          step="0.05"
          value={mood.novelty}
          onChange={(e) => handleSliderChange('novelty', e.target.value)}
          className="w-full h-1.5 bg-parchment-200/80 rounded-lg appearance-none cursor-pointer accent-terracotta touch-none"
        />
      </div>
    </div>
  );
}