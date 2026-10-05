import { defineConfig } from 'vite'

// El minificador de CSS por defecto (lightningcss) falla con el CSS de KaTeX que trae Slidev 53,
// y la alternativa (esbuild) no viene instalada. Sin minificar, el CSS pesa un poco más y nada cambia.
export default defineConfig({
  build: { cssMinify: false },
})
