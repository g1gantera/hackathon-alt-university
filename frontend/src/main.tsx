import React from 'react';
import {createRoot} from 'react-dom/client';
import {ConfigProvider,App as AntApp,theme as antTheme} from 'antd';
import ruRU from 'antd/locale/ru_RU';
import kkKZ from 'antd/locale/kk_KZ';
import enGB from 'antd/locale/en_GB';
import {useLanguage,initializeLanguage} from './language';
import App from './App';
import {useTheme} from './theme';
import {installLiquidGlass} from './liquid';
import './style.css';
import './dashboard.css';
import './shell.css';

function Root(){
 const language=useLanguage();
 const dark=useTheme(s=>s.theme)==='dark';
 return <ConfigProvider locale={{ru:ruRU,kk:kkKZ,en:enGB}[language]} theme={{algorithm:dark?antTheme.darkAlgorithm:antTheme.defaultAlgorithm,token:{colorPrimary:dark?'#1f9e88':'#0f7f6f',borderRadius:12,fontFamily:"Manrope, 'Segoe UI', Arial, sans-serif",colorText:dark?'#e9f1f1':'#11262d',colorTextSecondary:dark?'#9fb1b5':'#5b6d74',colorBgElevated:dark?'#1d2529':'#ffffff'},components:{Button:{borderRadius:999,borderRadiusSM:999,borderRadiusLG:999,fontWeight:600},Select:{borderRadius:12},InputNumber:{borderRadius:12}}}}><AntApp><App/></AntApp></ConfigProvider>;
}

initializeLanguage();
installLiquidGlass();
createRoot(document.getElementById('root')!).render(<React.StrictMode><Root/></React.StrictMode>);
