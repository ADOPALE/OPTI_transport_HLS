import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
import math
import plotly.express as px
import plotly.graph_objects as go

@dataclass
class Job:
    id: str
    origine: str
    destination: str
    hub_origine: str
    fonction_support: str
    type_contenant: str
    quantite: float
    jour: str
    poids_estime: float = 0.0
    volume_estime: float = 0.0

@dataclass
class Tournee:
    id: str
    jour: str
    vehicule_type: str
    capacite_max: float
    hub_depart: str
    jobs: List[Job] = field(default_factory=list)
    itineraire: List[str] = field(default_factory=list)
    duree_totale: float = 0.0 # en minutes
    distance_totale: float = 0.0
    remplissage_actuel: float = 0.0

@dataclass
class Chauffeur:
    id: str
    tournees: List[Tournee] = field(default_factory=list)
    temps_travail_cumule: float = 0.0

class MoteurSimulation:
    def __init__(self, data: Dict, params: Dict):
        self.data = data
        self.params = params
        self.hubs = self._construire_hubs()
        self.flotte = self._preparer_flotte()
        self.jours = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    
    def _construire_hubs(self) -> Dict[str, str]:
        """Identifie les sites à distance 0 et les groupe en Hubs."""
        matrice_dist = self.data["matrice_distance"].copy()
        
        # Sécurité index
        if matrice_dist.index.dtype in ['int64', 'int32']:
            col_sites = matrice_dist.columns[0]
            matrice_dist = matrice_dist.set_index(col_sites)
        
        sites = matrice_dist.index.tolist()
        matrice_dist.columns = sites 

        mapping_site_hub = {site: site for site in sites}
        
        for i, site_a in enumerate(sites):
            for site_b in sites[i+1:]:
                try:
                    if matrice_dist.at[site_a, site_b] == 0:
                        hub_id = mapping_site_hub[site_a]
                        mapping_site_hub[site_b] = hub_id
                except KeyError:
                    continue
        return mapping_site_hub

    def _preparer_flotte(self) -> pd.DataFrame:
        df_v = self.data["param_vehicules"].copy()
        flotte_sel = self.params.get("flotte_selectionnee", df_v["Type"].tolist())
        # On s'assure que le DataFrame est trié par capacité décroissante pour l'algo
        df_v['cap_tri'] = df_v.apply(lambda x: self._calculer_capacite_vehicule(x), axis=1)
        df_v = df_v.sort_values('cap_tri', ascending=False)
        return df_v[df_v["Type"].isin(flotte_sel)]

    def _calculer_capacite_vehicule(self, row_vehicule) -> float:
        tx_remplissage = self.params.get("taux_remplissage_max", 0.9)
        cols = [str(c).lower() for c in row_vehicule.index]
        if "capacite_contenants" in cols:
            return row_vehicule["Capacite_contenants"] * tx_remplissage
        return 10.0 

    def _filtrer_et_normaliser_flux(self) -> pd.DataFrame:
        df_flux = self.data["m_flux"].copy()
        col_nature = [c for c in df_flux.columns if "nature" in c.lower()]
        if not col_nature:
            return df_flux
        mask = df_flux[col_nature[0]].astype(str).str.lower().str.contains("volume")
        return df_flux[mask]

    def convertir_flux_en_jobs(self) -> List[Job]:
        df_flux = self._filtrer_et_normaliser_flux()
        jobs = []
        for idx, row in df_flux.iterrows():
            orig = str(row.get("Origine", ""))
            dest = str(row.get("Destination", ""))
            fs = str(row.get("Fonction Support", "Generique"))
            cont = str(row.get("Type contenant", "Standard"))
            for jour in self.jours:
                qte = row.get(jour, 0)
                if qte > 0:
                    jobs.append(Job(
                        id=f"J{idx}_{jour}",
                        origine=orig,
                        destination=dest,
                        hub_origine=self.hubs.get(orig, orig),
                        fonction_support=fs,
                        type_contenant=cont,
                        quantite=float(qte),
                        jour=jour
                    ))
        return jobs

    def flux_compatibles(self, tournee: Tournee, job: Job) -> bool:
        return job.hub_origine == tournee.hub_depart

    def calculer_temps_manutention(self, site: str) -> float:
        return 15.0 

    def simuler(self):
        all_jobs = self.convertir_flux_en_jobs()
        tournees_finales = []
        for jour in self.jours:
            jobs_du_jour = [j for j in all_jobs if j.jour == jour]
            jobs_du_jour.sort(key=lambda x: x.quantite, reverse=True)
            tournees_jour = self._construire_tournees_jour(jour, jobs_du_jour)
            tournees_finales.extend(tournees_jour)
        chauffeurs = self.affecter_tournees_aux_chauffeurs(tournees_finales)
        return self.generer_outputs(tournees_finales, chauffeurs)

    def _construire_tournees_jour(self, jour:
