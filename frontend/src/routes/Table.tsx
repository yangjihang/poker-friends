import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { API } from "../lib/api";
import { GameSocket, ServerMessage } from "../lib/ws";
import { useAuth } from "../store/auth";
import { Card } from "../components/Card";
import { Seat, BetChip, useCountdown } from "../components/Seat";
import { BettingControls } from "../components/BettingControls";

const BOT_TIERS: Array<{ key: string; label: string }> = [
  { key: "rookie", label: "菜鸟" },
  { key: "regular", label: "常规" },
  { key: "patron", label: "常客" },
  { key: "semi_pro", label: "半职业" },
  { key: "pro", label: "职业" },
];

// Position a seat on an ellipse inside the table container.
// visualIdx=0 is bottom-center (me), going clockwise.
function seatPosition(visualIdx: number, total: number) {
  const angle = Math.PI / 2 + (visualIdx / total) * Math.PI * 2;
  const rx = 42;
  const ry = 40;
  const x = 50 + rx * Math.cos(angle);
  const y = 50 + ry * Math.sin(angle);
  // Bet chip sits ~60% of the way from seat toward pot center.
  const chipX = 50 + rx * 0.55 * Math.cos(angle);
  const chipY = 50 + ry * 0.55 * Math.sin(angle);
  return { x, y, chipX, chipY };
}

function fmtDelta(n: number, bb: number): string {
  const v = n / bb;
  const abs = Math.abs(v);
  const text = abs < 10 ? abs.toFixed(1) : Math.round(abs).toString();
  return `${n > 0 ? "+" : "-"}${text}BB`;
}

function fmtCountdown(ms: number): string {
  if (ms <= 0) return "已到期";
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

export default function Table() {
  const { code = "" } = useParams();
  const token = useAuth((s) => s.token)!;
  const navigate = useNavigate();
  const [state, setState] = useState<any>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [showdown, setShowdown] = useState<Record<number, string[]> | null>(null);
  const [handSummary, setHandSummary] = useState<any>(null);
  const [sitModal, setSitModal] = useState<{ seat: number } | null>(null);
  const [botModal, setBotModal] = useState<{ seat: number } | null>(null);
  const [rebuyOpen, setRebuyOpen] = useState(false);
  const [buyin, setBuyin] = useState(0);
  const wsRef = useRef<GameSocket | null>(null);

  useEffect(() => {
    const ws = new GameSocket(code, token, (m: ServerMessage) => {
      if (m.type === "state") {
        setState(m);
      } else if (m.type === "event") {
        if (m.kind === "hand_start") {
          setShowdown(null);
          setHandSummary(null);
          setFlash("新一手开始");
          setTimeout(() => setFlash(null), 1000);
        }
      } else if (m.type === "hand_end") {
        setShowdown(m.data.showdown ?? null);
        setHandSummary(m.data);
      } else if (m.type === "error") {
        setFlash(`错误：${m.msg}`);
        setTimeout(() => setFlash(null), 1800);
      }
    });
    wsRef.current = ws;
    ws.connect();
    API.getRoom(code).then((info) => setBuyin(info.buyin_max)).catch(() => {});
    return () => ws.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code, token]);

  const mySeat = state?.your_seat_idx;
  const engine = state?.engine;
  const room = state?.room;

  const visualSeats = useMemo(() => {
    if (!room) return [];
    const max = room.max_seats;
    const rawSeats: any[] = room.seats;
    const anchor = mySeat ?? 0;
    return rawSeats.map((seat) => {
      const visual = (seat.seat_idx - anchor + max) % max;
      const pos = seatPosition(visual, max);
      return { ...seat, _visual: visual, _pos: pos };
    });
  }, [room, mySeat]);

  const isMyTurn = mySeat != null && engine?.actor_seat === mySeat;
  const bb = room?.bb ?? 1;

  function sendAction(action: string, amount?: number) {
    wsRef.current?.send({ type: "action", action, amount });
  }
  function doSit(seat: number, buyinValue: number) {
    wsRef.current?.send({ type: "sit", seat_idx: seat, buyin: buyinValue });
    setSitModal(null);
  }
  function doAddBot(seat: number, tier: string) {
    wsRef.current?.send({ type: "add_bot", seat_idx: seat, tier });
    setBotModal(null);
  }
  function doRebuy(buyinValue: number) {
    wsRef.current?.send({ type: "rebuy", buyin: buyinValue });
    setRebuyOpen(false);
  }

  const mySeatData = room?.seats?.find((s: any) => s.seat_idx === mySeat);
  const isBusted = mySeat != null && mySeatData && !mySeatData.empty && mySeatData.stack < (room?.bb ?? 1);

  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const closesMs = room?.closes_at ? new Date(room.closes_at).getTime() - nowMs : null;
  const closeUrgent = closesMs != null && closesMs > 0 && closesMs < 10 * 60 * 1000;

  if (!room) {
    return <div className="flex items-center justify-center h-screen">连接中…</div>;
  }

  const board = engine?.board || {};
  const boardCards: string[] = [
    ...((board.flop as string[]) || []),
    board.turn,
    board.river,
  ].filter(Boolean);

  const engineSeat = (seatIdx: number) =>
    engine?.seats?.find((s: any) => s.seat_idx === seatIdx);

  const winnerBySeat: Record<number, number> =
    handSummary?.winner_summary
      ? Object.fromEntries(
          (handSummary.winner_summary as any[]).map((w) => [w.seat_idx, w.net])
        )
      : {};

  const winners = handSummary?.winner_summary
    ? (handSummary.winner_summary as any[]).filter((w) => w.net > 0)
    : [];

  return (
    <div className="h-screen w-screen flex flex-col bg-gradient-to-b from-black via-zinc-900 to-black overflow-hidden">
      <header className="flex items-center justify-between px-3 py-2 bg-black/50 text-sm z-20">
        <button onClick={() => navigate("/")} className="px-2 py-1 rounded bg-white/10">← 大厅</button>
        <div className="font-mono text-xs opacity-80 flex gap-2 items-center">
          <span>{room.name} · {room.code} · {room.sb}/{room.bb}</span>
          {closesMs != null && !room.closed && (
            <span className={closeUrgent ? "text-red-300" : "text-chip-gold"}>
              · 关闭 {fmtCountdown(closesMs)}
            </span>
          )}
        </div>
        {mySeat != null ? (
          <div className="flex gap-1">
            {isBusted && (
              <button
                onClick={() => { setBuyin(room.buyin_max); setRebuyOpen(true); }}
                className="px-2 py-1 rounded bg-chip-gold text-black font-semibold"
              >
                再买入
              </button>
            )}
            <button
              onClick={() => wsRef.current?.send({ type: "stand" })}
              className="px-2 py-1 rounded bg-white/10"
            >
              离席
            </button>
          </div>
        ) : (
          <span className="w-12" />
        )}
      </header>

      <div className="flex-1 relative">
        <div className="absolute inset-x-4 inset-y-8 md:inset-x-20 rounded-[50%] bg-gradient-to-b from-[#0b4a38] to-[#07301f] border-[6px] border-black/60 shadow-[inset_0_0_40px_rgba(0,0,0,0.6)]" />

        {/* Center pot + board */}
        <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 flex flex-col items-center gap-2 z-[1]">
          <div className="flex gap-1 min-h-[4rem] items-center">
            <AnimatePresence>
              {boardCards.map((c, i) => (
                <motion.div
                  key={`${c}-${i}`}
                  initial={{ y: -24, opacity: 0 }}
                  animate={{ y: 0, opacity: 1 }}
                  transition={{ delay: i * 0.08 }}
                >
                  <Card code={c} />
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
          {(engine?.pot ?? 0) > 0 && (
            <div className="px-3 py-0.5 rounded-full bg-black/60 text-sm">
              {(engine.pot / bb).toFixed(bb === 1 ? 0 : 1)}BB
            </div>
          )}
          {state?.your_best_hand && state?.your_hole_cards && (
            <motion.div
              key={state.your_best_hand}
              initial={{ opacity: 0, y: -4 }}
              animate={{ opacity: 1, y: 0 }}
              className="px-3 py-0.5 rounded-full bg-chip-gold/20 border border-chip-gold/60 text-chip-gold text-xs font-semibold"
            >
              当前牌型：{state.your_best_hand}
            </motion.div>
          )}
          {!engine && !handSummary && (
            <div className="text-xs opacity-60">等待玩家入座…</div>
          )}
        </div>

        {/* Seats */}
        {visualSeats.map((seat: any) => {
          const eng = engineSeat(seat.seat_idx);
          const merged = eng ? { ...seat, ...eng } : seat;
          const isMe = seat.seat_idx === mySeat;
          const isActor = engine?.actor_seat === seat.seat_idx;
          const isButton = engine?.button_seat === seat.seat_idx;
          let cards: string[] | "hidden" | null = null;
          if (showdown && showdown[seat.seat_idx]) cards = showdown[seat.seat_idx];
          else if (isMe && state?.your_hole_cards) cards = state.your_hole_cards;
          else if (engine && !seat.empty && !merged.folded) cards = "hidden";
          return (
            <div
              key={seat.seat_idx}
              className="absolute -translate-x-1/2 -translate-y-1/2 z-[2]"
              style={{ left: `${seat._pos.x}%`, top: `${seat._pos.y}%` }}
            >
              <Seat
                seat={merged}
                isMe={isMe}
                isActor={isActor}
                isButton={isButton}
                cards={cards}
                bb={bb}
                deadlineMs={isActor ? engine?.actor_deadline_ms : null}
                timeoutS={engine?.action_timeout_s ?? 15}
                canSit={mySeat == null}
                onSit={() => setSitModal({ seat: seat.seat_idx })}
                onAddBot={() => setBotModal({ seat: seat.seat_idx })}
              />
            </div>
          );
        })}

        {/* Bet chips (fly from seat to chip-pot when bet changes) */}
        <AnimatePresence>
          {visualSeats.map((seat: any) => {
            const bet = engineSeat(seat.seat_idx)?.bet ?? 0;
            if (!bet) return null;
            // include bet amount in key so a new chip animates from seat each
            // time the amount increases (bet/raise).
            return (
              <motion.div
                key={`bet-${seat.seat_idx}-${bet}`}
                initial={{
                  left: `${seat._pos.x}%`,
                  top: `${seat._pos.y}%`,
                  scale: 0.4,
                  opacity: 0,
                }}
                animate={{
                  left: `${seat._pos.chipX}%`,
                  top: `${seat._pos.chipY}%`,
                  scale: 1,
                  opacity: 1,
                }}
                exit={{ left: "50%", top: "50%", scale: 0.5, opacity: 0 }}
                transition={{ type: "spring", stiffness: 220, damping: 22 }}
                className="absolute -translate-x-1/2 -translate-y-1/2 z-[3]"
              >
                <BetChip amount={bet} bb={bb} />
              </motion.div>
            );
          })}
        </AnimatePresence>

        {/* Per-seat net delta at hand end (+X / -X BB floating above avatar) */}
        <AnimatePresence>
          {handSummary && visualSeats.map((seat: any) => {
            if (seat.empty) return null;
            const net = winnerBySeat[seat.seat_idx];
            if (net === undefined || net === 0) return null;
            return (
              <motion.div
                key={`delta-${handSummary.hand_no}-${seat.seat_idx}`}
                initial={{ opacity: 0, y: 10, scale: 0.6 }}
                animate={{ opacity: 1, y: -28, scale: 1.2 }}
                exit={{ opacity: 0, y: -40 }}
                transition={{ duration: 0.4, delay: 0.7 }}
                className={`absolute -translate-x-1/2 -translate-y-1/2 font-extrabold text-base drop-shadow-md z-[5] ${
                  net > 0 ? "text-green-400" : "text-red-400"
                }`}
                style={{ left: `${seat._pos.x}%`, top: `${seat._pos.y}%` }}
              >
                {fmtDelta(net, bb)}
              </motion.div>
            );
          })}
        </AnimatePresence>

        {/* Pot-to-winner flying chip */}
        <AnimatePresence>
          {handSummary && winners.map((w: any) => {
            const seat = visualSeats.find((s: any) => s.seat_idx === w.seat_idx);
            if (!seat) return null;
            return (
              <motion.div
                key={`win-${handSummary.hand_no}-${w.seat_idx}`}
                initial={{ left: "50%", top: "50%", scale: 0.6, opacity: 0 }}
                animate={{
                  left: `${seat._pos.x}%`,
                  top: `${seat._pos.y}%`,
                  scale: [0.6, 1.4, 0.8],
                  opacity: [0, 1, 0],
                }}
                transition={{ duration: 0.9, delay: 0.1, times: [0, 0.45, 1] }}
                className="absolute -translate-x-1/2 -translate-y-1/2 z-[4] pointer-events-none"
              >
                <div className="w-8 h-8 rounded-full bg-gradient-to-br from-yellow-300 to-yellow-600 border-2 border-yellow-200 shadow-[0_0_14px_rgba(251,192,45,0.9)]" />
              </motion.div>
            );
          })}
        </AnimatePresence>

        <AnimatePresence>
          {flash && (
            <motion.div
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              className="absolute top-2 left-1/2 -translate-x-1/2 bg-black/70 text-xs px-3 py-1 rounded-full z-10"
            >
              {flash}
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <ActionBar
        isMyTurn={isMyTurn}
        deadlineMs={isMyTurn ? engine?.actor_deadline_ms : null}
      >
        {isMyTurn && engine?.legal ? (
          <BettingControls legal={engine.legal} pot={engine.pot} bb={bb} onAction={sendAction} />
        ) : isBusted ? (
          <div className="h-full flex flex-col items-center justify-center gap-2 px-4 py-6 pb-[calc(env(safe-area-inset-bottom)+0.5rem)]">
            <div className="text-sm text-white/70">筹码已耗尽</div>
            <button
              onClick={() => { setBuyin(room.buyin_max); setRebuyOpen(true); }}
              className="px-6 py-2 rounded-full bg-chip-gold text-black font-semibold"
            >
              再买入继续
            </button>
          </div>
        ) : (
          <div className="h-full flex items-center justify-center text-sm text-white/50 px-4 py-6 pb-[calc(env(safe-area-inset-bottom)+0.5rem)]">
            {engine?.actor_seat != null
              ? `等待 ${engineSeat(engine.actor_seat)?.display_name ?? "对手"} 行动…`
              : mySeat == null
                ? "观战中 — 点击空座位可坐下"
                : "等待发牌…"}
          </div>
        )}
      </ActionBar>

      {sitModal && (
        <Modal onClose={() => setSitModal(null)}>
          <div className="font-semibold mb-3">坐下座位 #{sitModal.seat}</div>
          <label className="block text-sm mb-3">
            带入筹码（{room.buyin_min}~{room.buyin_max}）
            <input
              type="number" value={buyin} onChange={(e) => setBuyin(+e.target.value)}
              min={room.buyin_min} max={room.buyin_max}
              className="mt-1 w-full bg-black/40 rounded px-2 py-1"
            />
          </label>
          <button onClick={() => doSit(sitModal.seat, buyin)} className="w-full bg-chip-gold text-black py-2 rounded-full font-semibold">
            确认
          </button>
        </Modal>
      )}

      {room.closed && room.final_standings && (
        <Modal onClose={() => navigate("/")}>
          <div className="font-bold text-lg mb-1">房间已关闭</div>
          <div className="text-xs opacity-70 mb-3">2 小时时限到，本场最终输赢：</div>
          <ul className="space-y-1 max-h-[50vh] overflow-auto">
            {(room.final_standings as any[]).map((s, i) => (
              <li
                key={s.display_name}
                className="flex justify-between items-center bg-black/30 rounded px-2 py-1"
              >
                <span className="text-sm">
                  <span className="opacity-60 mr-1">#{i + 1}</span>
                  {s.display_name}
                  {s.is_bot && <span className="opacity-50 text-xs"> (AI)</span>}
                </span>
                <span
                  className={`font-semibold ${
                    s.net > 0 ? "text-green-400" : s.net < 0 ? "text-red-400" : "opacity-70"
                  }`}
                >
                  {s.net === 0 ? "0" : fmtDelta(s.net, bb)}
                </span>
              </li>
            ))}
            {(room.final_standings as any[]).length === 0 && (
              <li className="text-sm opacity-60">这场没打完整一手</li>
            )}
          </ul>
          <button
            onClick={() => navigate("/")}
            className="mt-3 w-full bg-chip-gold text-black py-2 rounded-full font-semibold"
          >
            返回大厅
          </button>
        </Modal>
      )}

      {rebuyOpen && (
        <Modal onClose={() => setRebuyOpen(false)}>
          <div className="font-semibold mb-3">再买入</div>
          <label className="block text-sm mb-3">
            带入筹码（{room.buyin_min}~{room.buyin_max}）
            <input
              type="number" value={buyin} onChange={(e) => setBuyin(+e.target.value)}
              min={room.buyin_min} max={room.buyin_max}
              className="mt-1 w-full bg-black/40 rounded px-2 py-1"
            />
          </label>
          <button onClick={() => doRebuy(buyin)} className="w-full bg-chip-gold text-black py-2 rounded-full font-semibold">
            确认
          </button>
        </Modal>
      )}

      {botModal && (
        <Modal onClose={() => setBotModal(null)}>
          <div className="font-semibold mb-3">座位 #{botModal.seat} 添加机器人</div>
          <div className="grid grid-cols-2 gap-2">
            {BOT_TIERS.map((t) => (
              <button
                key={t.key}
                onClick={() => doAddBot(botModal.seat, t.key)}
                className="py-2 rounded bg-chip-blue"
              >
                {t.label}
              </button>
            ))}
          </div>
        </Modal>
      )}
    </div>
  );
}

function ActionBar({
  children,
  isMyTurn,
  deadlineMs,
}: {
  children: React.ReactNode;
  isMyTurn: boolean;
  deadlineMs: number | null | undefined;
}) {
  const remain = useCountdown(deadlineMs ?? 0);
  const urgent = isMyTurn && deadlineMs && remain <= 5;
  return (
    <div
      className={`shrink-0 min-h-[168px] border-t backdrop-blur transition-colors ${
        urgent
          ? "bg-red-900/60 border-red-500 animate-pulse"
          : "bg-black/80 border-white/10"
      }`}
    >
      {children}
    </div>
  );
}

function Modal({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 bg-black/60 z-30 flex items-end sm:items-center justify-center">
      <div className="bg-feltLight rounded-t-2xl sm:rounded-2xl p-4 w-full max-w-sm">
        <div className="flex justify-end mb-1">
          <button onClick={onClose} className="text-sm opacity-70">关闭</button>
        </div>
        {children}
      </div>
    </div>
  );
}
