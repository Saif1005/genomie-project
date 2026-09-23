/**
 * Mode de déploiement (build-time) : `local` (serveur on-premise, défaut) ou `aws`.
 * Ne change que les libellés et la validation des chemins ; l'API reste la même.
 */
export const DEPLOYMENT_MODE: 'local' | 'aws' =
  process.env.NEXT_PUBLIC_DEPLOYMENT_MODE === 'aws' ? 'aws' : 'local';

export const IS_LOCAL = DEPLOYMENT_MODE === 'local';

/** Racine des données sur le serveur (affichage des exemples uniquement). */
export const LOCAL_DATA_ROOT =
  process.env.NEXT_PUBLIC_LOCAL_DATA_ROOT || '/data/zaynb';

export const INPUT_LOCATION_LABEL = IS_LOCAL ? 'chemin serveur' : 'S3';
