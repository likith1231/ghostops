import DiffView from "./DiffView";

/** Just enough Markdown for GhostOps postmortems: headings, lists, checkboxes, tables, bold, code and diffs. */
function inline(text: string, key: number) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`|_[^_]+_)/g);
  return (
    <span key={key}>
      {parts.map((p, i) =>
        p.startsWith("**") ? <strong key={i} className="text-white">{p.slice(2, -2)}</strong>
        : p.startsWith("`") ? <code key={i} className="rounded bg-white/5 px-1 font-mono text-[12px] text-arc">{p.slice(1, -1)}</code>
        : p.startsWith("_") && p.endsWith("_") && p.length > 2 ? <em key={i} className="text-mist">{p.slice(1, -1)}</em>
        : p,
      )}
    </span>
  );
}

export default function Markdown({ source }: { source: string }) {
  const lines = source.split("\n");
  const out: React.ReactNode[] = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.startsWith("```")) {
      const body: string[] = [];
      for (i++; i < lines.length && !lines[i].startsWith("```"); i++) body.push(lines[i]);
      out.push(<DiffView key={i} diff={body.join("\n")} maxHeight="14rem" />);
    } else if (line.startsWith("|")) {
      const rows: string[][] = [];
      for (; i < lines.length && lines[i].startsWith("|"); i++) {
        if (!/^\|[-| ]+\|$/.test(lines[i])) rows.push(lines[i].slice(1, -1).split("|").map((c) => c.trim()));
      }
      i--;
      out.push(
        <div key={i} className="scrollbar-thin overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-mist">
              <tr>{rows[0]?.map((c, j) => <th key={j} className="border-b border-white/10 py-1.5 pr-3 font-medium">{c}</th>)}</tr>
            </thead>
            <tbody>
              {rows.slice(1).map((r, k) => (
                <tr key={k} className="border-b border-white/5">{r.map((c, j) => <td key={j} className="py-1.5 pr-3 text-white/80">{inline(c, j)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
    } else if (line.startsWith("# ")) out.push(<h2 key={i} className="text-xl font-semibold text-white">{line.slice(2)}</h2>);
    else if (line.startsWith("## ")) out.push(<h3 key={i} className="pt-2 font-mono text-xs uppercase tracking-[0.25em] text-arc">{line.slice(3)}</h3>);
    else if (line.startsWith("### ")) out.push(<h4 key={i} className="text-sm font-semibold text-white">{line.slice(4)}</h4>);
    else if (line.startsWith("- [ ] ")) out.push(<p key={i} className="flex gap-2 text-sm text-white/80"><span className="text-mist">☐</span>{inline(line.slice(6), i)}</p>);
    else if (line.startsWith("- ")) out.push(<p key={i} className="flex gap-2 text-sm text-white/80"><span className="text-arc">›</span>{inline(line.slice(2), i)}</p>);
    else if (line.trim()) out.push(<p key={i} className="text-sm text-white/80">{inline(line, i)}</p>);
  }
  return <div className="space-y-2">{out}</div>;
}
