import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Card } from "./Card";

type Props = {
  seat: any;
  isMe: boolean;
  isActor: boolean;
  isButton: boolean;
  cards: string[] | "hidden" | null;
  bb: number;
  deadlineMs?: number | null;
  timeoutS?: number;
  onSit?: () => void;
  onAddBot?: () => void;
  onRemoveBot?: () => void;
  canSit?: boolean;
};

export function useCountdown(deadlineMs: number) {
  const [remain, setRemain] = useState(() => Math.max(0, (deadlineMs - Date.now()) / 1000));
  useEffect(() => {
    const tick = () => setRemain(Math.max(0, (deadlineMs - Date.now()) / 1000));
    tick();
    const id = setInterval(tick, 100);
    return () => clearInterval(id);
  }, [deadlineMs]);
  return remain;
}

function TimerRing({ deadlineMs, timeoutS }: { deadlineMs: number; timeoutS: number }) {
  const remain = useCountdown(deadlineMs);
  const frac = Math.max(0, Math.min(1, remain / timeoutS));
  const C = 2 * Math.PI * 28;
  const dash = C * frac;
  const urgent = remain <= 5;
  const color = urgent ? "#ef4444" : remain < 10 ? "#fbbf24" : "#34d399";
  return (
    <svg
      className={`absolute inset-0 w-full h-full pointer-events-none -rotate-90 ${urgent ? "animate-pulse" : ""}`}
      viewBox="0 0 60 60"
    >
      <circle cx="30" cy="30" r="28" fill="none" stroke="rgba(255,255,255,0.12)" strokeWidth="3" />
      <circle
        cx="30" cy="30" r="28" fill="none"
        stroke={color} strokeWidth={urgent ? 4 : 3} strokeLinecap="round"
        strokeDasharray={`${dash} ${C - dash}`}
        style={{ transition: "stroke 200ms" }}
      />
      <g transform="rotate(90 30 30)">
        <text x="30" y="33" textAnchor="middle"
              className="fill-white font-bold" style={{ fontSize: 14 }}>
          {Math.ceil(remain)}
        </text>
      </g>
    </svg>
  );
}

function UrgentBadge({ deadlineMs }: { deadlineMs: number }) {
  const remain = useCountdown(deadlineMs);
  if (remain > 5) return null;
  return (
    <motion.div
      key="urgent"
      initial={{ scale: 0.5, opacity: 0 }}
      animate={{ scale: [1, 1.15, 1], opacity: 1 }}
      transition={{ scale: { repeat: Infinity, duration: 0.6 }, opacity: { duration: 0.15 } }}
      className="absolute -bottom-2 left-1/2 -translate-x-1/2 bg-red-600 text-white px-2 py-0.5 rounded-full text-xs font-extrabold shadow-[0_0_12px_rgba(239,68,68,0.9)] whitespace-nowrap z-10"
    >
      ⏱ {Math.ceil(remain)}s
    </motion.div>
  );
}

function formatBB(stack: number, bb: number): string {
  if (!bb) return String(stack);
  const v = stack / bb;
  if (Math.abs(v - Math.round(v)) < 0.05) return `${Math.round(v)}BB`;
  return `${v.toFixed(1)}BB`;
}

function avatarInitials(name: string): string {
  if (!name) return "?";
  const ch = name.trim()[0];
  return ch;
}

export function Seat({ seat, isMe, isActor, isButton, cards, bb, deadlineMs, timeoutS, onSit, onAddBot, onRemoveBot, canSit }: Props) {
  if (seat.empty) {
    return (
      <div className="flex flex-col items-center gap-1">
        <div className="w-14 h-14 rounded-full border-2 border-dashed border-white/25 bg-black/20" />
        <div className="flex gap-1">
          <button
            onClick={onSit}
            disabled={!canSit}
            className="text-[10px] px-2 py-0.5 bg-chip-gold text-black rounded disabled:opacity-30 disabled:bg-white/10 disabled:text-white"
          >
            坐下
          </button>
          <button
            onClick={onAddBot}
            className="text-[10px] px-2 py-0.5 bg-chip-blue rounded"
          >
            +AI
          </button>
        </div>
      </div>
    );
  }
  const dim = seat.folded ? "opacity-50" : "";
  return (
    <motion.div
      layout
      animate={{ scale: isActor ? 1.04 : 1 }}
      className={`flex flex-col items-center gap-0.5 ${dim}`}
    >
      <div className={`text-[11px] leading-tight max-w-[88px] text-center break-words ${isMe ? "text-chip-gold" : "text-white/90"}`}>
        {seat.display_name}
        {seat.is_bot && <span className="opacity-60"> ({seat.bot_tier})</span>}
      </div>
      <div className="relative">
        <div
          className={`relative w-14 h-14 rounded-full flex items-center justify-center bg-gradient-to-br from-white/20 to-black/50 border-2 overflow-visible ${
            isActor
              ? "border-chip-gold shadow-[0_0_14px_rgba(251,192,45,0.8)]"
              : isMe
                ? "border-chip-gold/70"
                : "border-white/40"
          }`}
        >
          <span className="text-xl font-bold select-none">{avatarInitials(seat.display_name)}</span>
          {isActor && deadlineMs && timeoutS && (
            <TimerRing deadlineMs={deadlineMs} timeoutS={timeoutS} />
          )}
          {isActor && deadlineMs && <UrgentBadge deadlineMs={deadlineMs} />}
        </div>
        {cards && (
          <div className="absolute -top-1 -right-2 flex gap-[2px]">
            {cards === "hidden" ? (
              <>
                <Card hidden small />
                <Card hidden small />
              </>
            ) : (
              cards.map((c, i) => <Card key={i} code={c} small />)
            )}
          </div>
        )}
        {isButton && (
          <span className="absolute -bottom-1 -left-1 text-[10px] bg-chip-gold text-black rounded-full w-5 h-5 flex items-center justify-center font-bold shadow">
            D
          </span>
        )}
        {seat.is_bot && onRemoveBot && (
          <button
            onClick={onRemoveBot}
            title="踢掉这个 AI"
            className="absolute -top-1 -left-1 w-5 h-5 rounded-full bg-red-600 hover:bg-red-500 text-white text-[11px] leading-none flex items-center justify-center shadow z-10"
          >
            ✕
          </button>
        )}
      </div>
      <div className="mt-0.5 px-2 py-0.5 rounded-full bg-black/60 text-[11px]">
        {formatBB(seat.stack, bb)}
      </div>
      {seat.sitting_out && !seat.is_bot && (
        <div className="text-[10px] text-white/60">离桌</div>
      )}
    </motion.div>
  );
}

export function BetChip({ amount, bb }: { amount: number; bb: number }) {
  if (!amount) return null;
  return (
    <motion.div
      layout
      initial={{ scale: 0, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-black/70 text-[11px] text-chip-gold"
    >
      <span className="w-2.5 h-2.5 rounded-full bg-chip-gold border border-yellow-700" />
      {formatBB(amount, bb)}
    </motion.div>
  );
}
