import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { API, HandDetail, HandSummary } from "../lib/api";
import { HandReplay } from "../components/HandReplay";

export default function Hands() {
  const [list, setList] = useState<HandSummary[]>([]);
  const [detail, setDetail] = useState<HandDetail | null>(null);
  const [showRaw, setShowRaw] = useState(false);

  useEffect(() => {
    API.myHands().then(setList).catch(() => {});
  }, []);

  async function open(id: number) {
    const d = await API.handDetail(id);
    setDetail(d);
  }

  return (
    <div className="min-h-screen px-4 py-6 max-w-3xl mx-auto">
      <header className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-bold">手牌历史</h1>
        <Link to="/" className="text-sm px-2 py-1 rounded bg-white/10">← 大厅</Link>
      </header>
      {!detail && (
        <ul className="space-y-2">
          {list.length === 0 && <li className="opacity-70 text-sm">还没打过牌</li>}
          {list.map((h) => (
            <li key={h.hand_id}>
              <button
                onClick={() => open(h.hand_id)}
                className="w-full flex justify-between items-center bg-black/30 rounded px-3 py-2 text-left"
              >
                <div>
                  <div className="text-sm">房间 {h.room_id} 第 {h.hand_no} 手</div>
                  <div className="text-xs opacity-60">{h.ended_at}</div>
                </div>
                <div className={h.net > 0 ? "text-green-400" : h.net < 0 ? "text-red-400" : "opacity-70"}>
                  {h.net > 0 ? `+${h.net}` : h.net}
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
      {detail && (
        <div className="space-y-3">
          <div className="flex justify-between items-center">
            <button onClick={() => { setDetail(null); setShowRaw(false); }} className="text-sm px-2 py-1 rounded bg-white/10">← 返回列表</button>
            <button
              onClick={() => setShowRaw((v) => !v)}
              className="text-sm px-2 py-1 rounded bg-white/10"
            >
              {showRaw ? "看回放" : "看文本详情"}
            </button>
          </div>
          <div className="bg-feltLight rounded-lg p-3">
            <div className="font-semibold mb-1">第 {detail.hand_no} 手 · 盲注 {detail.sb}/{detail.bb}</div>
            <div className="text-xs opacity-70">按钮座位 {detail.button_seat} · 底池 {detail.pot_total}</div>
          </div>

          {!showRaw && <HandReplay detail={detail} />}

          {showRaw && (
            <>
              <div className="bg-feltLight rounded-lg p-3">
                <div className="font-semibold mb-2">动作序列</div>
                <ol className="text-sm space-y-1">
                  {detail.actions.map((a: any) => (
                    <li key={a.seq} className="grid grid-cols-5 gap-2">
                      <span className="opacity-60">[{a.street}]</span>
                      <span className="truncate">{a.actor_name}</span>
                      <span>{a.action_type}</span>
                      <span>{a.amount ?? ""}</span>
                      <span className="opacity-60">池 {a.pot_after}</span>
                    </li>
                  ))}
                </ol>
              </div>
              <div className="bg-feltLight rounded-lg p-3">
                <div className="font-semibold mb-2">底牌</div>
                <ul className="text-sm space-y-1">
                  {detail.hole_cards.map((h: any) => (
                    <li key={h.seat_idx} className="flex justify-between">
                      <span>座位 {h.seat_idx}</span>
                      <span>{h.cards ? h.cards.join(" ") : (h.shown ? "—" : "未亮")}</span>
                    </li>
                  ))}
                </ul>
              </div>
              <div className="bg-feltLight rounded-lg p-3">
                <div className="font-semibold mb-2">结算</div>
                <ul className="text-sm space-y-1">
                  {(detail.winner_summary || []).map((w: any) => (
                    <li key={w.seat_idx} className="flex justify-between">
                      <span>座位 {w.seat_idx}</span>
                      <span className={w.net > 0 ? "text-green-400" : w.net < 0 ? "text-red-400" : ""}>
                        {w.net > 0 ? `+${w.net}` : w.net}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
