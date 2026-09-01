/**
 * prismjs ships no types for its core-only entry point. Point it at the real
 * `prismjs` typings rather than letting it fall through to `any` -- strict mode
 * with no `any` is an acceptance criterion.
 */
declare module 'prismjs/components/prism-core' {
  const Prism: typeof import('prismjs');
  export default Prism;
}
