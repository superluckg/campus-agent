export function formatSourcePages(pageNumbers?: number[], pageNumber?: number | null): string {
  const pages = [...new Set((pageNumbers?.length ? pageNumbers : [pageNumber])
    .filter((page): page is number => typeof page === 'number' && Number.isInteger(page) && page > 0))]
    .sort((a, b) => a - b);
  if (!pages.length) return '';
  const groups: string[] = [];
  let first = pages[0], previous = first;
  for (const page of pages.slice(1)) {
    if (page === previous + 1) { previous = page; continue; }
    groups.push(first === previous ? String(first) : `${first}–${previous}`);
    first = previous = page;
  }
  groups.push(first === previous ? String(first) : `${first}–${previous}`);
  return `第${groups.join('、')}页`;
}
