/// <reference types="vite/client" />

// Vite ?worker&url query — returns the URL string of a compiled worker script
declare module '*?worker&url' {
  const src: string
  export default src
}
