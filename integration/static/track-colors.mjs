export const trackCategories = [
  ['main', 'Главный путь', '#253047'], ['line', 'Без указанного назначения', '#87939b'],
  ['branch', 'Ветвь', '#3487c8'], ['industrial', 'Промышленный', '#967344'],
  ['siding', 'Приёмо-отправочный', '#19a58d'], ['yard', 'Станционный парк', '#24a8c7'],
  ['spur', 'Подъездной / тупиковый', '#9b5de5'], ['crossover', 'Соединительный', '#ff9874'],
  ['subway', 'Метро', '#f32735'], ['light_rail', 'Лёгкий рельсовый', '#00aaa3'],
  ['tram', 'Трамвай', '#ee487f'], ['narrow_gauge', 'Узкоколейный', '#64985f'],
];
export function trackCategory(properties) {
  if (['subway','light_rail','tram','narrow_gauge'].includes(properties.railway)) return properties.railway;
  if (['siding','yard','spur','crossover'].includes(properties.service)) return properties.service;
  if (['main','branch','industrial'].includes(properties.usage)) return properties.usage;
  return 'line';
}
