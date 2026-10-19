export default function DiffView({ diff, maxHeight = "18rem" }: { diff: string; maxHeight?: string }) {
  if (!diff.trim()) {
    return <p className="font-mono text-xs text-mist">No diff.</p>;
  }
  return (
    <pre
      className="scrollbar-thin overflow-auto rounded-xl border border-white/5 bg-black/50 p-3 font-mono text-[12px] leading-relaxed"
      style={{ maxHeight }}
    >
      {diff.split("\n").map((line, i) => {
        const cls = line.startsWith("+++") || line.startsWith("---")
          ? "text-mist"
          : line.startsWith("+")
            ? "bg-ok/10 text-ok"
            : line.startsWith("-")
              ? "bg-bad/10 text-bad"
              : line.startsWith("@@")
                ? "text-violet"
                : "text-white/70";
        return (
          <div key={i} className={`${cls} -mx-3 px-3 whitespace-pre`}>
            {line || " "}
          </div>
        );
      })}
    </pre>
  );
}
