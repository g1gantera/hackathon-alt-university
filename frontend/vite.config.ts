import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({plugins:[react()],server:{proxy:{'/api':'http://127.0.0.1:8000','/ws':{target:'ws://127.0.0.1:8000',ws:true,changeOrigin:false}}},build:{rollupOptions:{input:{main:'index.html',stage3:'stage3.html'},output:{manualChunks:{maps:['maplibre-gl'],charts:['echarts'],ui:['antd']}}}}});
