/**
 * Widens the literal string types produced by `as const` into plain `string`,
 * while preserving the object shape.
 *
 * The English catalogue is declared `as const` so translation keys can be
 * derived as a union of dotted paths. Typing a translation as `typeof en`
 * would then demand the exact same literals ("Save" and nothing else), which
 * is obviously wrong for a translation. `Catalogue<typeof en>` keeps the
 * structural requirement -- every key must exist, nesting must match -- but
 * lets each leaf hold any string.
 */
export type Catalogue<T> = {
  [K in keyof T]: T[K] extends string ? string : Catalogue<T[K]>
}
