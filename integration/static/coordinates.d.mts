import type {Topology, Train} from '../../frontend/src/types';
export function createLocator(topology:Topology): (train:Train)=>[number,number]|null;
export function trackIndex(train:Train, topology:Topology):number;

export function connectorFeatures(topology:Topology):any;
export function movementGeometry(topology:Topology,section:Topology['sections'][number],trackId?:string,fromTrack?:string|null,toTrack?:string|null):{fraction:number;coordinate:[number,number]}[];
