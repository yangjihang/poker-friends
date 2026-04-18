import { useEffect, useState } from "react";

type Legal = {
  can_fold: boolean;
  can_check: boolean;
  can_call: boolean;
  call_amount: number;
  can_raise: boolean;
  min_raise_to: number;
  max_raise_to: number;
};

function fmtBB(n: number, bb: number): string {
  if (!bb || bb === 1) return String(n);
  const v = n / bb;
  if (Math.abs(v - Math.round(v)) < 0.05) return `${Math.round(v)}BB`;
  return `${v.toFixed(1)}BB`;
}

export function BettingControls({
  legal,
  pot,
  bb,
  onAction,
}: {
  legal: Legal;
  pot: number;
  bb: number;
  onAction: (action: string, amount?: number) => void;
}) {
  const [amount, setAmount] = useState(legal.min_raise_to);
  useEffect(() => {
    setAmount((prev) =>
      Math.max(legal.min_raise_to, Math.min(prev || legal.min_raise_to, legal.max_raise_to))
    );
  }, [legal.min_raise_to, legal.max_raise_to]);

  const quick = (multiplier: number) => {
    const target = Math.round(pot * multiplier) + legal.call_amount;
    setAmount(Math.max(legal.min_raise_to, Math.min(target, legal.max_raise_to)));
  };

  return (
    <div className="px-3 py-2 pb-[calc(env(safe-area-inset-bottom)+0.5rem)]">
      {legal.can_raise && (
        <>
          <div className="flex gap-1 mb-2 justify-between items-center text-xs">
            <div className="flex gap-1">
              <button onClick={() => quick(0.5)} className="px-2 py-1 rounded bg-white/15">½池</button>
              <button onClick={() => quick(0.75)} className="px-2 py-1 rounded bg-white/15">¾池</button>
              <button onClick={() => quick(1)} className="px-2 py-1 rounded bg-white/15">底池</button>
              <button onClick={() => setAmount(legal.max_raise_to)} className="px-2 py-1 rounded bg-white/15">全下</button>
            </div>
            <input
              type="number"
              min={legal.min_raise_to}
              max={legal.max_raise_to}
              value={amount}
              onChange={(e) => setAmount(+e.target.value)}
              className="w-20 text-right bg-black/40 px-2 py-1 rounded text-xs"
            />
          </div>
          <input
            type="range"
            min={legal.min_raise_to}
            max={legal.max_raise_to}
            value={amount}
            onChange={(e) => setAmount(+e.target.value)}
            className="w-full mb-2 accent-chip-gold"
          />
        </>
      )}
      <div className="flex gap-2">
        <button
          disabled={!legal.can_fold}
          onClick={() => onAction("fold")}
          className="flex-1 py-3 rounded-xl bg-red-700 disabled:opacity-30 font-semibold text-sm"
        >
          弃牌
        </button>
        {legal.can_check ? (
          <button
            onClick={() => onAction("check")}
            className="flex-1 py-3 rounded-xl bg-gray-600 font-semibold text-sm"
          >
            过牌
          </button>
        ) : (
          <button
            disabled={!legal.can_call}
            onClick={() => onAction("call")}
            className="flex-1 py-3 rounded-xl bg-blue-700 disabled:opacity-30 font-semibold text-sm"
          >
            跟注 {fmtBB(legal.call_amount, bb)}
          </button>
        )}
        <button
          disabled={!legal.can_raise}
          onClick={() => onAction("raise", amount)}
          className="flex-1 py-3 rounded-xl bg-chip-gold text-black disabled:opacity-30 font-semibold text-sm"
        >
          加注 {fmtBB(amount, bb)}
        </button>
      </div>
    </div>
  );
}
