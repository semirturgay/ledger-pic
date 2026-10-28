"use client";

import { ChevronDown, ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { currentMonth, monthLabel, shiftMonth } from "@/lib/format";

const MONTHS = Array.from({ length: 12 }, (_, i) => String(i + 1).padStart(2, "0"));

export function MonthPicker({ month, onChange }: { month: string; onChange: (month: string) => void }) {
  const latest = currentMonth();
  const [open, setOpen] = useState(false);
  const [year, setYear] = useState(Number(month.slice(0, 4)));
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const closeOnOutside = (event: PointerEvent) => !ref.current?.contains(event.target as Node) && setOpen(false);
    const closeOnEscape = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    document.addEventListener("pointerdown", closeOnOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  function pick(next: string) {
    onChange(next);
    setOpen(false);
  }

  return (
    <div className="month-picker" ref={ref}>
      <button className="btn btn-ghost btn-sm btn-icon" aria-label="Previous month" onClick={() => onChange(shiftMonth(month, -1))}>
        <ChevronLeft size={18} />
      </button>
      <button
        className="btn btn-ghost btn-sm month-picker-label"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => {
          setYear(Number(month.slice(0, 4)));
          setOpen(!open);
        }}
      >
        {monthLabel(month)} <ChevronDown size={14} />
      </button>
      <button
        className="btn btn-ghost btn-sm btn-icon"
        aria-label="Next month"
        disabled={month >= latest}
        onClick={() => onChange(shiftMonth(month, 1))}
      >
        <ChevronRight size={18} />
      </button>

      {open && (
        <div className="month-panel" role="dialog" aria-label="Choose a month">
          <div className="month-panel-year">
            <button className="btn btn-ghost btn-sm btn-icon" aria-label="Previous year" onClick={() => setYear(year - 1)}>
              <ChevronLeft size={16} />
            </button>
            <strong>{year}</strong>
            <button
              className="btn btn-ghost btn-sm btn-icon"
              aria-label="Next year"
              disabled={year >= Number(latest.slice(0, 4))}
              onClick={() => setYear(year + 1)}
            >
              <ChevronRight size={16} />
            </button>
          </div>
          <div className="month-panel-grid">
            {MONTHS.map((m) => {
              const value = `${year}-${m}`;
              return (
                <button
                  key={m}
                  className={`btn btn-sm${value === month ? " btn-primary" : " btn-ghost"}`}
                  disabled={value > latest}
                  onClick={() => pick(value)}
                >
                  {monthLabel(value, "short")}
                </button>
              );
            })}
          </div>
          {month !== latest && (
            <button className="btn btn-sm btn-block" onClick={() => pick(latest)}>
              Jump to this month
            </button>
          )}
        </div>
      )}
    </div>
  );
}
