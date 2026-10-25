/** 只转换已登记的正文引用，跳过代码与链接。 */
interface Node {
  type: string;
  value?: string;
  tagName?: string;
  children?: Node[];
  properties?: Record<string, unknown>;
  position?: { start: { offset?: number }; end: { offset?: number } };
}

export function citationSuperscripts(options: { indexes: number[] }) {
  const allowed = new Set(options.indexes);
  return (tree: Node, file?: { value?: unknown }) => {
    function visit(parent: Node) {
      if (["code", "pre", "a", "sup"].includes(parent.tagName ?? "")) return;
      if (!parent.children) return;
      parent.children = parent.children.flatMap(child => {
        if (child.type !== "text" || !child.value) {
          visit(child);
          return [child];
        }
        const nodes: Node[] = [];
        let offset = 0;
        const raw = typeof file?.value === "string" && child.position
          ? file.value.slice(child.position.start.offset, child.position.end.offset) : child.value;
        const rawReferences = [...raw.matchAll(/(\\*)\[(\d+)\]/g)];
        let rawIndex = 0;
        for (const match of child.value.matchAll(/\[(\d+)\]/g)) {
          const original = rawReferences[rawIndex++];
          if (original?.[1] && original[1].length % 2 === 1) continue;
          if (!allowed.has(Number(match[1]))) continue;
          if (match.index > offset) nodes.push({ type: "text", value: child.value.slice(offset, match.index) });
          nodes.push({ type: "element", tagName: "sup", properties: { className: ["citation-reference"] },
            children: [{ type: "text", value: match[0] }] });
          offset = match.index + match[0].length;
        }
        if (!nodes.length) return [child];
        if (offset < child.value.length) nodes.push({ type: "text", value: child.value.slice(offset) });
        return nodes;
      });
    }
    visit(tree);
  };
}
