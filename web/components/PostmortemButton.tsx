"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useState } from "react";
import { api, type Postmortem } from "@/lib/api";
import Markdown from "./Markdown";

/** Opens the automatic postmortem for a run. */
export default function PostmortemButton({ runRef }: { runRef: string }) {
  const [doc, setDoc] = useState<Postmortem | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!doc) return;
    const key = (e: KeyboardEvent) => e.key === "Escape" && setDoc(null);
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [doc]);

  const open = async () => {
    setError(null);
    try {
      setDoc(await api.postmortem(runRef));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <>
      <button onClick={open} className="cut-corners border border-arc/30 bg-arc/5 px-3 py-1.5 font-mono text-xs text-white transition hover:bg-arc/15">
        📄 postmortem
      </button>
      {error && <span className="ml-2 text-xs text-bad">{error}</span>}
      <AnimatePresence>
        {doc && (
          <motion.div className="fixed inset-0 z-[200] grid place-items-center bg-void/80 p-4 backdrop-blur-md" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={() => setDoc(null)}>
            <motion.div role="dialog" aria-label={doc.title} className="glass scrollbar-thin max-h-[85vh] w-full max-w-3xl overflow-y-auto rounded-3xl p-6" initial={{ y: 24 }} animate={{ y: 0 }} onClick={(e) => e.stopPropagation()}>
              <div className="mb-4 flex justify-end">
                <button onClick={() => setDoc(null)} className="font-mono text-xs text-mist hover:text-white">esc ✕</button>
              </div>
              <Markdown source={doc.markdown} />
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
