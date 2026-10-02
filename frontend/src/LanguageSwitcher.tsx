import {useLanguage} from './language';
import {setLanguage,translate,type Language} from './i18n/core';
import './language.css';

export function LanguageSwitcher(){
 const language=useLanguage();
 return <select className="rf-language" value={language} onChange={event=>setLanguage(event.target.value as Language)} aria-label={translate('Язык')} title={translate('Язык')}>
  <option value="kk" lang="kk">Қазақша</option>
  <option value="ru" lang="ru">Русский</option>
  <option value="en" lang="en">English</option>
 </select>;
}
