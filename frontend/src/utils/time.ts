/** 所有面向用户的日期时间固定为北京时间，独立于浏览器所在时区。 */
const formatter = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
});

export function formatBeijingTime(
  value: string | null | undefined, seconds = false, separator = " · ",
): string {
  if (!value) return "—";
  // 缺少时区的时间值按 UTC 解析，避免随本机时区漂移。
  const explicit = /[T ]\d{2}:\d{2}/.test(value) && !/(Z|[+-]\d{2}:?\d{2})$/i.test(value)
    ? `${value.replace(" ", "T")}Z` : value;
  const timestamp = Date.parse(explicit);
  if (!Number.isFinite(timestamp)) return "—";
  const parts = Object.fromEntries(formatter.formatToParts(timestamp).map(part => [part.type, part.value]));
  return `${parts.year}/${parts.month}/${parts.day}${separator}${parts.hour}:${parts.minute}${seconds ? `:${parts.second}` : ""}`;
}

export function beijingExport<T>(value: T): T {
  if (Array.isArray(value)) return value.map(item => beijingExport(item)) as T;
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, original]) => {
      let item = original;
      if (key.endsWith("_at") && typeof item === "string" && /T.*(Z|[+-]\d{2}:?\d{2})$/i.test(item)) {
        const timestamp = Date.parse(item);
        if (Number.isFinite(timestamp)) {
          item = new Date(timestamp + 8 * 60 * 60 * 1000).toISOString().replace("Z", "+08:00");
        }
      }
      return [key, beijingExport(item)];
    })) as T;
  }
  return value;
}
