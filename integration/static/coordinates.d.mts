import type {Topology, Train} from '../../frontend/src/types';
export function createLocator(topology:Topology): (train:Train)=>[number,number]|null;
export function trackIndex(train:Train, topology:Topology):number;
