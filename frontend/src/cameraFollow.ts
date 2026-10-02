import type {Coordinate} from './rail3d/geography';

/** Constrain the center during wheel/pinch/rotation without cancelling their zoom animation. */
export function followCenter(active:boolean,following:boolean,focusing:boolean,coordinate?:Coordinate){
 return active&&following&&!focusing?coordinate:undefined;
}
