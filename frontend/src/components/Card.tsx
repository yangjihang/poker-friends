import { motion } from "framer-motion";

const SUIT_CHAR: Record<string, string> = { s: "♠", h: "♥", d: "♦", c: "♣" };
const RED = new Set(["h", "d"]);

export function Card({ code, hidden, small }: { code?: string | null; hidden?: boolean; small?: boolean }) {
  const w = small ? "w-8 h-12 text-[10px]" : "w-12 h-16 sm:w-14 sm:h-20 text-sm";
  if (hidden || !code) {
    return (
      <motion.div
        initial={{ rotateY: 180 }}
        animate={{ rotateY: 0 }}
        className={`${w} rounded-md bg-gradient-to-br from-blue-800 to-blue-950 border border-white/20 shadow-md`}
      />
    );
  }
  const rank = code[0] === "T" ? "10" : code[0];
  const suit = code[1];
  const red = RED.has(suit);
  return (
    <motion.div
      initial={{ scale: 0.8, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      className={`${w} rounded-md bg-white text-black shadow-md flex flex-col items-center justify-center font-bold border border-gray-300`}
    >
      <span className={red ? "text-red-600" : "text-black"}>{rank}</span>
      <span className={`${red ? "text-red-600" : "text-black"} text-lg leading-none`}>{SUIT_CHAR[suit] ?? suit}</span>
    </motion.div>
  );
}
