import React from 'react';
import {createRoot} from 'react-dom/client';
import {ConfigProvider,App as AntApp} from 'antd';
import ruRU from 'antd/locale/ru_RU';
import App from './App';
import './style.css';
createRoot(document.getElementById('root')!).render(<React.StrictMode><ConfigProvider locale={ruRU} theme={{token:{colorPrimary:'#137f70',borderRadius:8,fontFamily:'Inter, Segoe UI, Arial, sans-serif',colorText:'#223b44'}}}><AntApp><App/></AntApp></ConfigProvider></React.StrictMode>);
