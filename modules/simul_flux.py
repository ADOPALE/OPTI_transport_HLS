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
    duree_totale: float = 0.0 
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
        matrice_dist = self.data["matrice_distance"].copy()
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
                except:
                    continue
        return mapping_site_hub

    def _preparer_flotte(self) -> pd.DataFrame:
        df_v = self.data["param_vehicules"].copy()
        # Utilisation de "Types" (avec un s) correspondant à votre CSV 
        col_name = "Types" if "Types" in df_v.columns else df_v.columns[0]
        flotte_sel = self.params.get("flotte_selectionnee", df_v[col_name].tolist())
        
        df_v['cap_tri'] = df_v.apply(lambda x: self._calculer_capacite_vehicule(x), axis=1)
        df_v = df_v.sort_values('cap_tri', ascending=False)
        return df_v[df_v[col_name].isin(flotte_sel)]

    def _calculer_capacite_vehicule(self, row_vehicule) -> float:
        tx_remplissage = self.params.get("taux_remplissage_max", 0.9)
        # Votre CSV utilise "Poids max chargement" 
        if "Poids max chargement" in row_vehicule:
            try:
                val = str(row_vehicule["Poids max chargement"]).split(' ')[0].replace(',', '.')
                return float(val) * tx_remplissage
            except:
                return 10.0
        return 10.0 

    def _filtrer_et_normaliser_flux(self) -> pd.DataFrame:
        df_flux = self.data["m_flux"].copy()
        # Nom de colonne exact de votre fichier M flux.csv [cite: 2]
        col_nature = "Nature du flux (les tournées sont elles à prévoir avec une obligation de transport ou une obligation de passage?)"
        if col_nature in df_flux.columns:
            return df_flux[df_flux[col_nature].astype(str).str.contains("Volume", na=False)]
        return df_flux

    def convertir_flux_en_jobs(self) -> List[Job]:
        df_flux = self._filtrer_et_normaliser_flux()
        jobs = []
        for idx, row in df_flux.iterrows():
            # Mappage sur vos colonnes CSV [cite: 2]
            orig = str(row.get("Point de départ", ""))
            dest = str(row.get("Point de destination", ""))
            fs = str(row.get("Fonction Support associée", "Generique"))
            cont = str(row.get("Nature de contenant", "Standard"))
            
            for jour in self.jours:
                # Gestion de l'espace dans "Quantité Lundi " [cite: 2]
                col_jour = f"Quantité {jour} " if jour != "Dimanche" else "Quantité Dimanche "
                qte = row.get(col_jour, 0)
                try:
                    val_qte = float(str(qte).replace(',', '.')) if pd.notnull(qte) and str(qte).strip() != "" else 0
                    if val_qte > 0:
                        jobs.append(Job(
                            id=f"J{idx}_{jour}", origine=orig, destination=dest,
                            hub_origine=self.hubs.get(orig, orig), fonction_support=fs,
                            type_contenant=cont, quantite=val_qte, jour=jour
                        ))
                except:
                    continue
        return jobs

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

    def _construire_tournees_jour(self, jour: str, jobs: List[Job]) -> List[Tournee]:
        tournees = []
        jobs_restants = jobs.copy()
        while jobs_restants:
            job = jobs_restants.pop(0)
            insere = False
            for t in tournees:
                if job.hub_origine == t.hub_depart and (t.remplissage_actuel + job.quantite <= t.capacite_max):
                    t.jobs.append(job)
                    t.remplissage_actuel += job.quantite
                    if job.destination not in t.itineraire:
                        t.itineraire.append(job.destination)
                    self._recalculer_metriques_tournee(t)
                    insere = True
                    break
            if not insere:
                v_row = self.flotte.iloc[0]
                # Utilisation de "Types" ici pour corriger votre KeyError 
                v_type = v_row["Types"] if "Types" in v_row else v_row.index[0]
                new_t = Tournee(
                    id=f"T_{jour}_{len(tournees)+1}",
                    jour=jour,
                    vehicule_type=v_type,
                    capacite_max=self._calculer_capacite_vehicule(v_row),
                    hub_depart=job.hub_origine
                )
                new_t.jobs.append(job)
                new_t.remplissage_actuel = job.quantite
                new_t.itineraire.append(job.destination)
                self._recalculer_metriques_tournee(new_t)
                tournees.append(new_t)
        return tournees

    def _recalculer_metriques_tournee(self, t: Tournee):
        duree, dist = 0.0, 0.0
        curr = t.hub_depart
        for stop in t.itineraire:
            try:
                duree += self.data["matrice_duree"].at[curr, stop]
                dist += self.data["matrice_distance"].at[curr, stop]
                duree += 15.0 # Manutention fixe
            except: pass
            curr = stop
        try:
            duree += self.data["matrice_duree"].at[curr, t.hub_depart]
            dist += self.data["matrice_distance"].at[curr, t.hub_depart]
        except: pass
        t.duree_totale, t.distance_totale = duree, dist

    def affecter_tournees_aux_chauffeurs(self, tournees: List[Tournee]) -> List[Chauffeur]:
        amp_max = self.params.get("contraintes_rh", {}).get("amplitude_max", 450)
        chauffeurs = []
        for jour in self.jours:
            t_jour = sorted([t for t in tournees if t.jour == jour], key=lambda x: x.duree_totale, reverse=True)
            ch_jour = []
            for t in t_jour:
                aff = False
                for c in ch_jour:
                    if c.temps_travail_cumule + t.duree_totale <= amp_max:
                        c.tournees.append(t); c.temps_travail_cumule += t.duree_totale
                        aff = True; break
                if not aff:
                    new_c = Chauffeur(id=f"CH_{jour}_{len(ch_jour)+1}")
                    new_c.tournees.append(t); new_c.temps_travail_cumule = t.duree_totale
                    ch_jour.append(new_c)
            chauffeurs.extend(ch_jour)
        return chauffeurs

    def generer_outputs(self, tournees: List[Tournee], chauffeurs: List[Chauffeur]) -> Dict:
        """Produit les DataFrames de synthèse et les KPIs attendus par l'interface."""
        df_t = pd.DataFrame([{
            "ID Tournée": t.id, 
            "Jour": t.jour, 
            "Véhicule": t.vehicule_type,
            "Hub Origine": t.hub_depart, 
            "Arrêts": " > ".join(t.itineraire),
            "Nb Jobs": len(t.jobs), 
            "Distance (km)": round(t.distance_totale, 1),
            "Durée (min)": round(t.duree_totale, 1),
            "Remplissage (%)": round((t.remplissage_actuel / t.capacite_max)*100, 1) if t.capacite_max > 0 else 0
        } for t in tournees])
        
        # Calcul du besoin max de chauffeurs par jour
        nb_par_jour = []
        for j in self.jours:
            count = len([c for c in chauffeurs if c.id.startswith(f"CH_{j}")])
            nb_par_jour.append(count)
        
        # Structure de retour
        res = {
            "tournees": df_t, 
            "obj_chauffeurs": chauffeurs,
            "kpis": {
                "nb_tournees": len(tournees),
                "nb_chauffeurs_max_jour": max(nb_par_jour) if nb_par_jour else 0,
                "distance_totale": df_t["Distance (km)"].sum() if not df_t.empty else 0,
                "temps_total_heures": (df_t["Durée (min)"].sum() / 60) if not df_t.empty else 0
            }
        }
        
        # Organisation pour l'affichage par onglet (ex: "Quantité Lundi")
        for j in self.jours:
            res[f"Quantité {j}"] = {
                "chauffeurs": [c.__dict__ for c in chauffeurs if c.id.startswith(f"CH_{j}")]
            }
        
        return res
