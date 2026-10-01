import type {Signal} from './types';
// Original vector artwork, based on the mast/head form in the supplied reference.
export function signalMarkup(signal:Signal){
 const code=signal.type==='entry'?'Вх':signal.type==='warning'?'Пр':'Вых';
 const lamps=(signal.type==='warning'?['yellow','green']:['yellow','green','red']).map((color,i)=>`<circle cx="12" cy="${10+i*12}" r="4" fill="${signal.aspect===color?({yellow:'#ffda4e',green:'#36ed87',red:'#ff5353'} as Record<string,string>)[color]:'#3c484b'}"/>`).join('');
 return `<svg xmlns="http://www.w3.org/2000/svg" width="30" height="72" viewBox="0 0 30 72" aria-label="${code}"><path d="M12 40V62M5 63H19" stroke="#758386" stroke-width="3"/><rect x="4" y="2" width="16" height="40" rx="7" fill="#172126" stroke="#ced8d9"/>${lamps}<rect x="0" y="47" width="28" height="13" rx="2" fill="white"/><text x="14" y="57" text-anchor="middle" font-size="9" fill="#22333a">${code}</text></svg>`;
}
