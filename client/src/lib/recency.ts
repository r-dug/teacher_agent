/**
 * Lightweight localStorage-backed recency tracking for dropdown options.
 *
 * Each "bucket" (e.g. "persona", "voice") stores an ordered list of IDs,
 * most-recently-used first.  sortByRecency() puts recent items at the top,
 * with the remainder sorted alphabetically by display key.
 */

const MAX_RECENT = 10

function storageKey(bucket: string) {
  return `recency_${bucket}`
}

export function getRecent(bucket: string): string[] {
  try {
    return JSON.parse(localStorage.getItem(storageKey(bucket)) ?? '[]') as string[]
  } catch {
    return []
  }
}

export function recordRecent(bucket: string, id: string): void {
  const prev = getRecent(bucket).filter((x) => x !== id)
  localStorage.setItem(storageKey(bucket), JSON.stringify([id, ...prev].slice(0, MAX_RECENT)))
}

/**
 * Sort items: recently-used ones first (ordered by recency), then the rest
 * sorted alphabetically by the result of `getLabel`.
 */
export function sortByRecency<T>(
  items: T[],
  getId: (item: T) => string,
  getLabel: (item: T) => string,
  bucket: string,
): T[] {
  const recent = getRecent(bucket)
  return [...items].sort((a, b) => {
    const ra = recent.indexOf(getId(a))
    const rb = recent.indexOf(getId(b))
    if (ra !== -1 && rb !== -1) return ra - rb          // both recent → by recency order
    if (ra !== -1) return -1                             // only a is recent → a first
    if (rb !== -1) return 1                              // only b is recent → b first
    return getLabel(a).localeCompare(getLabel(b))        // neither → alphabetical
  })
}
