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

  // 10 档快捷 size：0.1 ~ 3 pot + 全下。Flex-wrap 允许手机端换行。
  const sizes: Array<{ label: string; apply: () => void }> = [
    { label: "⅒", apply: () => quick(0.1) },
    { label: "⅕", apply: () => quick(0.2) },
    { label: "⅓", apply: () => quick(1 / 3) },
    { label: "½", apply: () => quick(0.5) },
    { label: "¾", apply: () => quick(0.75) },
    { label: "1池", apply: () => quick(1) },
    { label: "1.5池", apply: () => quick(1.5) },
    { label: "2池", apply: () => quick(2) },
    { label: "3池", apply: () => quick(3) },
    { label: "全下", apply: () => setAmount(legal.max_raise_to) },
  ];

  return (
    <div className="px-3 py-2 pb-[calc(env(safe-area-inset-bottom)+0.5rem)]">
      {legal.can_raise && (
        <>
          <div className="flex flex-wrap gap-1 mb-2 items-center text-[11px]">
            {sizes.map((s) => (
              <button
                key={s.label}
                onClick={s.apply}
                className="px-1.5 py-0.5 rounded bg-white/15 hover:bg-white/25"
              >
                {s.label}
              </button>
            ))}
            <input
              type="number"
              min={legal.min_raise_to}
              max={legal.max_raise_to}
              value={amount}
              onChange={(e) => setAmount(+e.target.value)}
              className="ml-auto w-20 text-right bg-black/40 px-2 py-0.5 rounded"
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
