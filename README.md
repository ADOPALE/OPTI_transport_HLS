# OptiFLUX - controles stricts

Cette version garantit et controle simultanement :

- amplitude de chaque poste exactement egale a 450 minutes ;
- occupation maximale de chaque vehicule inferieure ou egale au plafond
  parametre, soit 85 % dans le resultat de controle.

Le moteur ne choisit plus de vehicule trop petit comme solution de secours.
Un regroupement qui depasse le plafond est refuse et doit etre scinde.

Le fichier `resultat_controle_amplitude_capacite.xlsx` verifie :

- 48 postes sur 48 a `07:30` ;
- occupation maximale reelle de 84,87 % ;
- aucune mission au-dessus de 85 %.

Ce resultat reste non final car deux flux sont encore non servis.
