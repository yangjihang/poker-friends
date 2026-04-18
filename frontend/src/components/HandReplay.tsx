import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Card } from "./Card";
import { HandDetail } from "../lib/api";

type SeatSnap = {
  seat_idx: number;
  display_name: string;
  is_bot: boolean;
  bot_tier?: string | null;
  stack: number;
  bet: number;
  folded: boolean;
  cards: string[] | null;
  winner_delta?: number;
};

type Frame = {
  label: string;
  street: string;
  board: string[];
  seats: Record<number, SeatSnap>;
  pot: number;
  actor_seat: number | null;
  is_final: boolean;
};

const STREET_LABEL: Record<string, string> = {
  preflop: "翻牌前",
  flop: "翻牌",
  turn: "转牌",
  river: "河牌",
};

const ACTION_LABEL: Record<string, string> = {
  fold: "弃牌",
  check: "过牌",
  call: "跟注",
  bet: "下注",
  raise: "加注",
};

function clone(seats: Record<number, SeatSnap>): Record<number, SeatSnap> {
  const out: Record<number, SeatSnap> = {};
  for (const [k, v] of Object.entries(seats)) out[+k] = { ...v };
  return out;
}

function fmtBB(amount: number, bb: number): string {
  if (!bb) return String(amount);
  const v = amount / bb;
  if (Math.abs(v - Math.round(v)) < 0.05) return `${Math.round(v)}BB`;
  return `${v.toFixed(1)}BB`;
}

function buildFrames(detail: HandDetail): Frame[] {
  const seatsMap = (detail.seats || {}) as Record<string, any>;
  const seatIdxs = Object.keys(seatsMap).map(Number).sort((a, b) => a - b);

  const holeBySeat: Record<number, string[] | null> = {};
  const shownBySeat: Record<number, boolean> = {};
  for (const hc of detail.hole_cards || []) {
    holeBySeat[hc.seat_idx] = hc.cards ?? null;
    shownBySeat[hc.seat_idx] = !!hc.shown;
  }

  // determine SB/BB seats by blind order from button
  const n = seatIdxs.length;
  const btnPos = seatIdxs.indexOf(detail.button_seat);
  const safeBtn = btnPos >= 0 ? btnPos : 0;
  let sbSeat: number, bbSeat: number;
  if (n === 2) {
    sbSeat = seatIdxs[safeBtn];
    bbSeat = seatIdxs[(safeBtn + 1) % n];
  } else {
    sbSeat = seatIdxs[(safeBtn + 1) % n];
    bbSeat = seatIdxs[(safeBtn + 2) % n];
  }

  const snap: Record<number, SeatSnap> = {};
  for (const idx of seatIdxs) {
    const info = seatsMap[String(idx)] || {};
    snap[idx] = {
      seat_idx: idx,
      display_name: info.display_name ?? `座位${idx}`,
      is_bot: !!info.is_bot,
      bot_tier: info.bot_tier ?? null,
      stack: info.starting_stack ?? 0,
      bet: 0,
      folded: false,
      cards: holeBySeat[idx] ?? null,
    };
  }

  // post blinds
  if (snap[sbSeat]) {
    snap[sbSeat].stack -= detail.sb;
    snap[sbSeat].bet = detail.sb;
  }
  if (snap[bbSeat]) {
    snap[bbSeat].stack -= detail.bb;
    snap[bbSeat].bet = detail.bb;
  }

  const frames: Frame[] = [];
  let pot = detail.sb + detail.bb;
  let currentStreet = "preflop";
  let board: string[] = [];
  const fullBoard: string[] = [
    ...((detail.board?.flop as string[]) || []),
    detail.board?.turn,
    detail.board?.river,
  ].filter(Boolean) as string[];

  frames.push({
    label: "发牌 · 盲注入池",
    street: "preflop",
    board: [],
    seats: clone(snap),
    pot,
    actor_seat: null,
    is_final: false,
  });

  for (const a of detail.actions || []) {
    // street advance before this action
    if (a.street !== currentStreet) {
      // clear previous street's bets (already in pot_after of prior action)
      for (const s of Object.values(snap)) s.bet = 0;
      const want =
        a.street === "flop" ? 3 : a.street === "turn" ? 4 : a.street === "river" ? 5 : 0;
      board = fullBoard.slice(0, want);
      currentStreet = a.street;
      frames.push({
        label: STREET_LABEL[a.street] ?? a.street,
        street: a.street,
        board: [...board],
        seats: clone(snap),
        pot,
        actor_seat: null,
        is_final: false,
      });
    }

    // apply the action
    const s = snap[a.seat_idx];
    if (s) {
      if (a.action_type === "fold") {
        s.folded = true;
      } else if (a.action_type === "check") {
        // nothing
      } else if (a.amount != null) {
        s.bet = a.amount;
        s.stack = a.stack_after ?? s.stack;
      } else {
        s.stack = a.stack_after ?? s.stack;
      }
    }
    pot = a.pot_after ?? pot;

    const amtLabel =
      a.action_type === "bet" || a.action_type === "raise"
        ? ` 至 ${fmtBB(a.amount, detail.bb)}`
        : a.action_type === "call"
          ? ` ${fmtBB(a.amount ?? 0, detail.bb)}`
          : "";
    frames.push({
      label: `${s?.display_name ?? `座位${a.seat_idx}`} · ${ACTION_LABEL[a.action_type] ?? a.action_type}${amtLabel}`,
      street: a.street,
      board: [...board],
      seats: clone(snap),
      pot,
      actor_seat: a.seat_idx,
      is_final: false,
    });
  }

  // Runout: if there are board cards not yet shown (all-in fast-forward),
  // reveal them one street at a time.
  for (const stage of [3, 4, 5]) {
    if (board.length >= stage || fullBoard.length < stage) continue;
    board = fullBoard.slice(0, stage);
    for (const s of Object.values(snap)) s.bet = 0;
    frames.push({
      label:
        stage === 3 ? STREET_LABEL.flop : stage === 4 ? STREET_LABEL.turn : STREET_LABEL.river,
      street: stage === 3 ? "flop" : stage === 4 ? "turn" : "river",
      board: [...board],
      seats: clone(snap),
      pot,
      actor_seat: null,
      is_final: false,
    });
  }

  // final frame: reveal shown hole cards, apply winner deltas
  for (const idx of seatIdxs) {
    if (shownBySeat[idx] && holeBySeat[idx]) snap[idx].cards = holeBySeat[idx];
    snap[idx].bet = 0;
  }
  const winMap: Record<number, number> = {};
  for (const w of detail.winner_summary || []) winMap[w.seat_idx] = w.net;
  for (const idx of seatIdxs) {
    const net = winMap[idx] ?? 0;
    if (net !== 0) snap[idx].winner_delta = net;
  }
  frames.push({
    label: "结算",
    street: currentStreet,
    board: [...board],
    seats: clone(snap),
    pot: detail.pot_total ?? pot,
    actor_seat: null,
    is_final: true,
  });

  return frames;
}

function seatPosition(visualIdx: number, total: number) {
  const angle = Math.PI / 2 + (visualIdx / total) * Math.PI * 2;
  const rx = 40;
  const ry = 38;
  const x = 50 + rx * Math.cos(angle);
  const y = 50 + ry * Math.sin(angle);
  const chipX = 50 + rx * 0.55 * Math.cos(angle);
  const chipY = 50 + ry * 0.55 * Math.sin(angle);
  return { x, y, chipX, chipY };
}

export function HandReplay({ detail }: { detail: HandDetail }) {
  const frames = useMemo(() => buildFrames(detail), [detail]);
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    setStep(0);
    setPlaying(false);
  }, [detail.hand_id]);

  useEffect(() => {
    if (!playing) {
      if (timerRef.current) window.clearTimeout(timerRef.current);
      timerRef.current = null;
      return;
    }
    if (step >= frames.length - 1) {
      setPlaying(false);
      return;
    }
    timerRef.current = window.setTimeout(() => {
      setStep((s) => Math.min(s + 1, frames.length - 1));
    }, 1200 / speed);
    return () => {
      if (timerRef.current) window.clearTimeout(timerRef.current);
    };
  }, [playing, step, frames.length, speed]);

  const frame = frames[step];
  const bb = detail.bb || 1;
  const seatIdxs = Object.keys(frame.seats).map(Number).sort((a, b) => a - b);

  // anchor the user's seat at bottom-center if they're in the hand
  const userSeatIdx = seatIdxs.find(
    (i) => (detail.seats as any)?.[String(i)]?.user_id != null && frame.seats[i].cards != null
  );
  const total = seatIdxs.length;
  const anchor = userSeatIdx ?? seatIdxs[0] ?? 0;
  const visualSeats = seatIdxs.map((idx) => {
    const anchorPos = seatIdxs.indexOf(anchor);
    const mePos = seatIdxs.indexOf(idx);
    const visual = (mePos - anchorPos + total) % total;
    return { idx, pos: seatPosition(visual, total), seat: frame.seats[idx] };
  });

  return (
    <div className="rounded-xl border border-white/10 bg-black/30 overflow-hidden">
      <div className="relative w-full aspect-[4/3] sm:aspect-[16/10] bg-gradient-to-b from-black to-zinc-900">
        <div className="absolute inset-x-4 inset-y-6 md:inset-x-16 rounded-[50%] bg-gradient-to-b from-[#0b4a38] to-[#07301f] border-[5px] border-black/60 shadow-[inset_0_0_30px_rgba(0,0,0,0.6)]" />

        {/* Board + pot */}
        <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 flex flex-col items-center gap-2 z-[1]">
          <div className="flex gap-1 min-h-[3.5rem] items-center">
            <AnimatePresence>
              {frame.board.map((c, i) => (
                <motion.div
                  key={`${c}-${i}`}
                  initial={{ y: -18, opacity: 0 }}
                  animate={{ y: 0, opacity: 1 }}
                  transition={{ delay: i * 0.06 }}
                >
                  <Card code={c} small />
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
          {frame.pot > 0 && (
            <div className="px-3 py-0.5 rounded-full bg-black/60 text-xs">
              底池 {fmtBB(frame.pot, bb)}
            </div>
          )}
        </div>

        {/* Seats */}
        {visualSeats.map(({ idx, pos, seat }) => {
          const isActor = frame.actor_seat === idx;
          const isButton = detail.button_seat === idx;
          return (
            <div
              key={idx}
              className="absolute -translate-x-1/2 -translate-y-1/2 z-[2]"
              style={{ left: `${pos.x}%`, top: `${pos.y}%` }}
            >
              <motion.div
                animate={{ scale: isActor ? 1.08 : 1 }}
                className="flex flex-col items-center gap-0.5"
              >
                <div
                  className={`text-[10px] max-w-[80px] truncate text-center ${
                    seat.folded ? "text-white/50" : "text-white/90"
                  }`}
                >
                  {seat.display_name}
                  {seat.is_bot && (
                    <span className="opacity-60"> ({seat.bot_tier})</span>
                  )}
                </div>
                <div className="relative">
                  <div
                    className={`relative w-11 h-11 rounded-full flex items-center justify-center bg-gradient-to-br from-white/20 to-black/50 border-2 ${
                      isActor
                        ? "border-chip-gold shadow-[0_0_10px_rgba(251,192,45,0.9)]"
                        : seat.folded
                          ? "border-white/20"
                          : "border-white/40"
                    } ${seat.folded ? "opacity-70" : ""}`}
                  >
                    <span className="text-sm font-bold">
                      {(seat.display_name || "?").trim()[0]}
                    </span>
                    {seat.folded && (
                      <span className="absolute inset-0 flex items-center justify-center text-[10px] font-extrabold text-red-400 bg-black/30 rounded-full">
                        弃
                      </span>
                    )}
                  </div>
                  {/* Cards area — always rendered so the seat visibly has cards,
                      even when folded (with a muted overlay). */}
                  <div className="absolute -top-1 -right-2 flex gap-[1px]">
                    {seat.folded ? (
                      <div className="relative opacity-50 grayscale">
                        <Card hidden small />
                      </div>
                    ) : seat.cards ? (
                      seat.cards.map((c, i) => <Card key={i} code={c} small />)
                    ) : (
                      <>
                        <Card hidden small />
                        <Card hidden small />
                      </>
                    )}
                  </div>
                  {isButton && (
                    <span className="absolute -bottom-1 -left-1 text-[9px] bg-chip-gold text-black rounded-full w-4 h-4 flex items-center justify-center font-bold">
                      D
                    </span>
                  )}
                </div>
                <div className="mt-0.5 px-1.5 py-0.5 rounded-full bg-black/60 text-[10px]">
                  {fmtBB(seat.stack, bb)}
                </div>
                {frame.is_final && seat.winner_delta && seat.winner_delta !== 0 && (
                  <div
                    className={`text-[10px] font-bold ${
                      seat.winner_delta > 0 ? "text-green-400" : "text-red-400"
                    }`}
                  >
                    {seat.winner_delta > 0 ? "+" : ""}
                    {fmtBB(seat.winner_delta, bb)}
                  </div>
                )}
              </motion.div>
            </div>
          );
        })}

        {/* Bet chips */}
        <AnimatePresence>
          {visualSeats.map(({ idx, pos, seat }) => {
            if (!seat.bet) return null;
            return (
              <motion.div
                key={`bet-${idx}-${seat.bet}`}
                initial={{ left: `${pos.x}%`, top: `${pos.y}%`, scale: 0.4, opacity: 0 }}
                animate={{
                  left: `${pos.chipX}%`,
                  top: `${pos.chipY}%`,
                  scale: 1,
                  opacity: 1,
                }}
                exit={{ left: "50%", top: "50%", scale: 0.5, opacity: 0 }}
                transition={{ type: "spring", stiffness: 220, damping: 22 }}
                className="absolute -translate-x-1/2 -translate-y-1/2 z-[3] flex items-center gap-1 px-2 py-0.5 rounded-full bg-black/70 text-[10px] text-chip-gold"
              >
                <span className="w-2 h-2 rounded-full bg-chip-gold border border-yellow-700" />
                {fmtBB(seat.bet, bb)}
              </motion.div>
            );
          })}
        </AnimatePresence>

        {/* Street badge top-left */}
        <div className="absolute top-2 left-2 text-[10px] px-2 py-0.5 rounded bg-black/60 text-white/80">
          {STREET_LABEL[frame.street] ?? frame.street}
        </div>
      </div>

      {/* Step label */}
      <div className="px-3 py-2 bg-black/40 text-sm text-center min-h-[2.25rem]">
        {frame.label}
      </div>

      {/* Controls */}
      <div className="flex items-center gap-2 px-3 py-2 bg-black/60">
        <button
          onClick={() => {
            setPlaying(false);
            setStep(0);
          }}
          className="px-2 py-1 rounded bg-white/10 text-xs"
        >
          ⏮
        </button>
        <button
          onClick={() => {
            setPlaying(false);
            setStep((s) => Math.max(0, s - 1));
          }}
          className="px-2 py-1 rounded bg-white/10 text-xs"
        >
          ◀
        </button>
        <button
          onClick={() => {
            if (step >= frames.length - 1) setStep(0);
            setPlaying((p) => !p);
          }}
          className="px-3 py-1 rounded bg-chip-gold text-black text-xs font-semibold"
        >
          {playing ? "暂停" : "播放"}
        </button>
        <button
          onClick={() => {
            setPlaying(false);
            setStep((s) => Math.min(frames.length - 1, s + 1));
          }}
          className="px-2 py-1 rounded bg-white/10 text-xs"
        >
          ▶
        </button>
        <button
          onClick={() => {
            setPlaying(false);
            setStep(frames.length - 1);
          }}
          className="px-2 py-1 rounded bg-white/10 text-xs"
        >
          ⏭
        </button>
        <select
          value={speed}
          onChange={(e) => setSpeed(+e.target.value)}
          className="ml-1 bg-black/40 text-xs rounded px-1 py-1"
        >
          <option value={0.5}>0.5x</option>
          <option value={1}>1x</option>
          <option value={1.5}>1.5x</option>
          <option value={2}>2x</option>
        </select>
        <input
          type="range"
          min={0}
          max={frames.length - 1}
          value={step}
          onChange={(e) => {
            setPlaying(false);
            setStep(+e.target.value);
          }}
          className="flex-1"
        />
        <span className="text-[10px] opacity-60 tabular-nums">
          {step + 1}/{frames.length}
        </span>
      </div>
    </div>
  );
}
