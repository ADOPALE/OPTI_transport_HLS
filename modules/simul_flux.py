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
        flotte_sel = self.params.get("flotte_selectionnee", df_v["Types"].tolist())
        # On s'assure que le DataFrame est trié par capacité décroissante pour l'algo
        df_v['cap_tri'] = df_v.apply(lambda x: self._calculer_capacite_vehicule(x), axis=1)
        df_v = df_v.sort_values('cap_tri', ascending=False)
        return df_v[df_v["Types"].isin(flotte_sel)]

    def _calculer_capacite_vehicule(self, row_vehicule) -> float:
        tx_remplissage = self.params.get("taux_remplissage_max", 0.9)
        cols = [str(c).lower() for c in row_vehicule.index]
        if "capacite_contenants" in cols:
            return row_vehicule["Capacite_contenants"] * tx_remplissage
        return 10.0 

    def _filtrer_et_normaliser_flux(self) -> pd.DataFrame:
        df_flux = self.data["m_flux"].copy()
        # Nom exact de la colonne dans votre fichier 
        col_filtre = "Nature du flux (les tournées sont elles à prévoir avec une obligation de transport ou une obligation de passage?)"
        
        if col_filtre in df_flux.columns:
            # On ne garde que les lignes marquées "Volume" 
            return df_flux[df_flux[col_filtre].astype(str).str.contains("Volume", na=False)]
        return df_flux

    def convertir_flux_en_jobs(self) -> List[Job]:
        df_flux = self._filtrer_et_normaliser_flux()
        jobs = []
        
        for idx, row in df_flux.iterrows():
            # Noms exacts de votre fichier CSV 
            orig = str(row.get("Point de départ", ""))
            dest = str(row.get("Point de destination", ""))
            fs = str(row.get("Fonction Support associée", "Generique"))
            cont = str(row.get("Nature de contenant", "Standard"))
            
            for jour in self.jours:
                # Gestion de l'espace final présent dans votre fichier 
                # Le fichier a "Quantité Lundi " mais "Quantité Dimanche" sans espace
                col_jour = f"Quantité {jour} " if jour != "Dimanche" else "Quantité Dimanche "
                qte = row.get(col_jour, 0)
                
                # Conversion sécurisée en nombre
                try:
                    if pd.notnull(qte) and str(qte).strip() != "":
                        val_qte = float(str(qte).replace(',', '.'))
                        if val_qte > 0:
                            jobs.append(Job(
                                id=f"J{idx}_{jour}",
                                origine=orig,
                                destination=dest,
                                hub_origine=self.hubs.get(orig, orig),
                                fonction_support=fs,
                                type_contenant=cont,
                                quantite=val_qte,
                                jour=jour
                            ))
                except (ValueError, TypeError):
                    continue
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

    def _construire_tournees_jour(self, jour: str, jobs: List[Job]) -> List[Tournee]:
        tournees = []
        jobs_restants = jobs.copy()
        while jobs_restants:
            job = jobs_restants.pop(0)
            insere = False
            for t in tournees:
                if self.flux_compatibles(t, job) and (t.remplissage_actuel + job.quantite <= t.capacite_max):
                    self._ajouter_job_a_tournee(t, job)
                    insere = True
                    break
            if not insere:
                v_row = self.flotte.iloc[0]
                cap = self._calculer_capacite_vehicule(v_row)
                new_t = Tournee(
                    id=f"T_{jour}_{len(tournees)+1}",
                    jour=jour,
                    vehicule_type=v_row["Type"],
                    capacite_max=cap,
                    hub_depart=job.hub_origine
                )
                self._ajouter_job_a_tournee(new_t, job)
                tournees.append(new_t)
        return tournees

    def _ajouter_job_a_tournee(self, t: Tournee, job: Job):
        t.jobs.append(job)
        t.remplissage_actuel += job.quantite
        if job.destination not in t.itineraire:
            t.itineraire.append(job.destination)
        self._recalculer_metriques_tournee(t)

    def _recalculer_metriques_tournee(self, t: Tournee):
        duree = 0.0
        dist = 0.0
        curr = t.hub_depart
        for stop in t.itineraire:
            try:
                duree += self.data["matrice_duree"].at[curr, stop]
                dist += self.data["matrice_distance"].at[curr, stop]
                duree += self.calculer_temps_manutention(stop)
            except:
                pass
            curr = stop
        try:
            duree += self.data["matrice_duree"].at[curr, t.hub_depart]
            dist += self.data["matrice_distance"].at[curr, t.hub_depart]
        except:
            pass
        t.duree_totale = duree
        t.distance_totale = dist

    def affecter_tournees_aux_chauffeurs(self, tournees: List[Tournee]) -> List[Chauffeur]:
        params_rh = self.params.get("contraintes_rh", {})
        amplitude_max = params_rh.get("amplitude_max", 450)
        chauffeurs = []
        for jour in self.jours:
            t_du_jour = sorted([t for t in tournees if t.jour == jour], key=lambda x: x.duree_totale, reverse=True)
            chauffeurs_jour = []
            for t in t_du_jour:
                affecte = False
                for c in chauffeurs_jour:
                    if c.temps_travail_cumule + t.duree_totale <= amplitude_max:
                        c.tournees.append(t)
                        c.temps_travail_cumule += t.duree_totale
                        affecte = True
                        break
                if not affecte:
                    new_c = Chauffeur(id=f"CH_{jour}_{len(chauffeurs_jour)+1}")
                    new_c.tournees.append(t)
                    new_c.temps_travail_cumule = t.duree_totale
                    chauffeurs_jour.append(new_c)
            chauffeurs.extend(chauffeurs_jour)
        return chauffeurs

    def generer_outputs(self, tournees: List[Tournee], chauffeurs: List[Chauffeur]) -> Dict:
        df_t = pd.DataFrame([{
            "ID Tournée": t.id,
            "Jour": t.jour,
            "Véhicule": t.vehicule_type,
            "Hub Origine": t.hub_depart,
            "Arrêts": " > ".join(t.itineraire),
            "Nb Jobs": len(t.jobs),
            "Remplissage (%)": round((t.remplissage_actuel / t.capacite_max)*100, 1) if t.capacite_max > 0 else 0,
            "Distance (km)": round(t.distance_totale, 1),
            "Durée (min)": round(t.duree_totale, 1)
        } for t in tournees])

        # Initialisation du dictionnaire de sortie
        res_final = {"tournees": df_t}
        
        # Organisation des chauffeurs par jour pour l'affichage 
        for j in self.jours:
            chauf_du_jour = [c for c in chauffeurs if c.id.startswith(f"CH_{j}")]
            # On stocke dans le format "Quantité Lundi" attendu par l'UI
            res_final[f"Quantité {j}"] = {"chauffeurs": [c.__dict__ for c in chauf_du_jour]}

        # Ajout des KPIs
        res_final["kpis"] = {
            "nb_tournees": len(tournees),
            "distance_totale": df_t["Distance (km)"].sum() if not df_t.empty else 0,
            "temps_total_heures": (df_t["Durée (min)"].sum() / 60) if not df_t.empty else 0
        }
        
        return res_final

def generer_visuel_bin_packing(tournee: Any, df_vehicules: pd.DataFrame, df_contenants: pd.DataFrame) -> go.Figure:
    # On gère si tournee est un dictionnaire ou un objet
    t_id = tournee.id if hasattr(tournee, 'id') else tournee.get('id', 'Inconnu')
    t_vtype = tournee.vehicule_type if hasattr(tournee, 'vehicule_type') else tournee.get('type_vehicule', 'Inconnu')
    t_jobs = tournee.jobs if hasattr(tournee, 'jobs') else tournee.get('jobs', [])

    vehicule_info = df_vehicules[df_vehicules["Type"] == t_vtype]
    if vehicule_info.empty:
        return go.Figure().update_layout(title="Véhicule inconnu")
    
    v_row = vehicule_info.iloc[0]
    V_L = v_row.get('Longueur', 6000) 
    V_W = v_row.get('Largeur', 2400)  
    
    contenants_a_placer = []
    destinations = list(set(j.destination if hasattr(j, 'destination') else j['destination'] for j in t_jobs))
    color_palette = px.colors.qualitative.Safe
    color_map = {dest: color_palette[i % len(color_palette)] for i, dest in enumerate(destinations)}

    for job in t_jobs:
        j_type = job.type_contenant if hasattr(job, 'type_contenant') else job['type_contenant']
        j_qte = job.quantite if hasattr(job, 'quantite') else job['quantite']
        j_dest = job.destination if hasattr(job, 'destination') else job['destination']
        j_fs = job.fonction_support if hasattr(job, 'fonction_support') else job['fonction_support']

        cont_info = df_contenants[df_contenants["Type"] == j_type]
        if cont_info.empty:
            c_l, c_w, c_h = 1200, 800, 1500
        else:
            c_row = cont_info.iloc[0]
            c_l = c_row.get('Longueur', 1200)
            c_w = c_row.get('Largeur', 800)
            c_h = c_row.get('Hauteur', 1500)

        for _ in range(int(j_qte)):
            contenants_a_placer.append({
                'l': c_l, 'w': c_w, 'h': c_h,
                'dest': j_dest, 'fs': j_fs, 'type': j_type,
                'color': color_map[j_dest]
            })

    contenants_a_placer.sort(key=lambda x: x['l'], reverse=True)
    
    rects = []
    cur_x, cur_y, shelf_h = 0, 0, 0
    for c in contenants_a_placer:
        if cur_y + c['w'] > V_W:
            cur_x += shelf_h
            cur_y, shelf_h = 0, 0
        
        rects.append({'x': cur_x, 'y': cur_y, 'l': c['l'], 'w': c['w'], 'info': c})
        cur_y += c['w']
        shelf_h = max(shelf_h, c['l'])

    fig = go.Figure()
    fig.add_shape(type="rect", x0=0, y0=0, x1=V_L, y1=V_W, line=dict(color="Black", width=3))

    for r in rects:
        c = r['info']
        fig.add_trace(go.Scatter(
            x=[r['x'], r['x']+r['l'], r['x']+r['l'], r['x'], r['x']],
            y=[r['y'], r['y'], r['y']+r['w'], r['y']+r['w'], r['y']],
            fill="toself", fillcolor=c['color'], mode='lines', line=dict(color="black", width=1),
            text=f"Dest: {c['dest']}<br>Type: {c['type']}", hoverinfo="text", showlegend=False
        ))

    fig.update_layout(title=f"Chargement {t_vtype}", xaxis_range=[0, V_L], yaxis_range=[0, V_W], height=400)
    return fig
