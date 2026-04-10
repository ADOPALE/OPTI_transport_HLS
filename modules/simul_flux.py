import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
import math

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
        matrice_dist = self.data["matrice_distance"]
        sites = matrice_dist.index.tolist()
        mapping_site_hub = {site: site for site in sites}
        
        # Algorithme simple de groupement par distance nulle
        for i, site_a in enumerate(sites):
            for site_b in sites[i+1:]:
                if matrice_dist.loc[site_a, site_b] == 0:
                    # On unifie sous le nom du premier site rencontré
                    hub_id = mapping_site_hub[site_a]
                    mapping_site_hub[site_b] = hub_id
        return mapping_site_hub

    def _preparer_flotte(self) -> pd.DataFrame:
        """Filtre la flotte sélectionnée par l'utilisateur."""
        df_v = self.data["param_vehicules"].copy()
        flotte_sel = self.params.get("flotte_selectionnee", df_v["Type"].tolist())
        return df_v[df_v["Type"].isin(flotte_sel)]

    def _calculer_capacite_vehicule(self, row_vehicule) -> float:
        """Calcule une capacité générique (priorité au nb de contenants ou volume)."""
        tx_remplissage = self.params.get("taux_remplissage_max", 0.9)
        # On cherche des colonnes de capacité
        cols = row_vehicule.index.str.lower()
        if "capacite_contenants" in cols:
            return row_vehicule["capacite_contenants"] * tx_remplissage
        elif "volume_utile" in cols:
            return row_vehicule["volume_utile"] * tx_remplissage
        return 10.0 # Valeur de repli par défaut (ex: 10 palettes)

    def _filtrer_et_normaliser_flux(self) -> pd.DataFrame:
        df_flux = self.data["m_flux"].copy()
        # Normalisation robuste du nom de colonne
        col_nature = [c for c in df_flux.columns if "nature" in c.lower() and "flux" in c.lower()]
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
        """Logique centralisée de compatibilité. Extensible."""
        # Exemple : Interdire certains mélanges si besoin (ex: Déchets + Repas)
        # Pour cette V1, on accepte tout ce qui vient du même hub d'origine
        if job.hub_origine != tournee.hub_depart:
            return False
        return True

    def calculer_temps_manutention(self, site: str) -> float:
        """Centralisation du temps fixe à quai (en minutes)."""
        return 15.0 # Hypothèse par défaut

    def simuler(self):
        all_jobs = self.convertir_flux_en_jobs()
        tournees_finales = []
        
        for jour in self.jours:
            jobs_du_jour = [j for j in all_jobs if j.jour == jour]
            # Trier les jobs par volume décroissant pour une heuristique plus efficace
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
            
            # 1. Tenter d'insérer dans une tournée existante
            insere = False
            for t in tournees:
                if self.flux_compatibles(t, job) and (t.remplissage_actuel + job.quantite <= t.capacite_max):
                    # Score d'insertion : coût marginal de temps
                    if self._est_pertinent_ajouter(t, job):
                        self._ajouter_job_a_tournee(t, job)
                        insere = True
                        break
            
            # 2. Sinon, créer une nouvelle tournée avec le plus gros véhicule dispo
            if not insere:
                v_row = self.flotte.iloc[0] # Hypothèse : trié par capacité
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

    def _est_pertinent_ajouter(self, tournee: Tournee, job: Job) -> bool:
        """Décide si l'ajout du job ne dégrade pas trop la tournée (heuristique)."""
        # Si la destination est déjà dans l'itinéraire, c'est forcément pertinent
        if job.destination in tournee.itineraire:
            return True
        # Sinon, limite de détour de 45 minutes
        return True 

    def _ajouter_job_a_tournee(self, t: Tournee, job: Job):
        t.jobs.append(job)
        t.remplissage_actuel += job.quantite
        if job.destination not in t.itineraire:
            t.itineraire.append(job.destination)
        self._recalculer_metriques_tournee(t)

    def _recalculer_metriques_tournee(self, t: Tournee):
        """Calcule distance et durée via les matrices."""
        duree = 0.0
        dist = 0.0
        curr = t.hub_depart
        
        for stop in t.itineraire:
            duree += self.data["matrice_duree"].loc[curr, stop]
            dist += self.data["matrice_distance"].loc[curr, stop]
            duree += self.calculer_temps_manutention(stop)
            curr = stop
            
        # Retour au hub
        duree += self.data["matrice_duree"].loc[curr, t.hub_depart]
        dist += self.data["matrice_distance"].loc[curr, t.hub_depart]
        
        t.duree_totale = duree
        t.distance_totale = dist

    def affecter_tournees_aux_chauffeurs(self, tournees: List[Tournee]) -> List[Chauffeur]:
        """Répartition des tournées par chauffeur (First Fit Decreasing)."""
        params_rh = self.params.get("contraintes_rh", {})
        amplitude_max = params_rh.get("amplitude_max", 450) # 7.5h par défaut
        
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
        """Produit les DataFrames de synthèse."""
        df_t = pd.DataFrame([{
            "ID Tournée": t.id,
            "Jour": t.jour,
            "Véhicule": t.vehicule_type,
            "Hub Origine": t.hub_depart,
            "Arrêts": " > ".join(t.itineraire),
            "Nb Jobs": len(t.jobs),
            "Remplissage (%)": round((t.remplissage_actuel / t.capacite_max)*100, 1),
            "Distance (km)": round(t.distance_totale, 1),
            "Durée (min)": round(t.duree_totale, 1)
        } for t in tournees])
        
        # Indicateurs globaux
        kpis = {
            "nb_tournees": len(tournees),
            "nb_chauffeurs_max_jour": max([len([c for c in chauffeurs if c.id.startswith(f"CH_{j}")]) for j in self.jours]),
            "distance_totale": df_t["Distance (km)"].sum(),
            "remplissage_moyen": df_t["Remplissage (%)"].mean(),
            "temps_total_heures": df_t["Durée (min)"].sum() / 60
        }
        
        return {"tournees": df_t, "kpis": kpis, "obj_chauffeurs": chauffeurs}

import plotly.graph_objects as go
import random

def generer_visuel_bin_packing(tournee: Tournee, df_vehicules: pd.DataFrame, df_contenants: pd.DataFrame) -> go.Figure:
    """
    Génère un visuel 2D (vue de dessus) du chargement du camion via une heuristique NFDH.
    Affiche quel contenant transporte quoi (Fonction Support) et vers où (Destination).
    """
    
    # 1. Récupération des dimensions du véhicule
    vehicule_info = df_vehicules[df_vehicules["Type"] == tournee.vehicule_type]
    if vehicule_info.empty:
        return go.Figure().update_layout(title="Véhicule inconnu")
    
    v_row = vehicule_info.iloc[0]
    # Normalisation robuste des colonnes de dimensions
    col_dim_v = {c.lower(): c for c in v_row.index}
    V_L = v_row.get(col_dim_v.get('longueur', ''), 6000) # mm
    V_W = v_row.get(col_dim_v.get('largeur', ''), 2400)  # mm
    
    # 2. Préparation de la liste des contenants à charger
    contenants_a_placer = []
    # Palette de couleurs génériques pour les destinations
    destinations = list(set(j.destination for j in tournee.jobs))
    couleurs = px.colors.qualitative.Safe
    color_map = {dest: couleurs[i % len(couleurs)] for i, dest in enumerate(destinations)}

    for job in tournee.jobs:
        cont_info = df_contenants[df_contenants["Type"] == job.type_contenant]
        if cont_info.empty:
            # Dimensions par défaut si inconnu (ex: palette standard)
            c_l, c_w, c_h = 1200, 800, 1500
        else:
            c_row = cont_info.iloc[0]
            col_dim_c = {c.lower(): c for c in c_row.index}
            c_l = c_row.get(col_dim_c.get('longueur', ''), 1200)
            c_w = c_row.get(col_dim_c.get('largeur', ''), 800)
            c_h = c_row.get(col_dim_c.get('hauteur', ''), 1500)

        for _ in range(int(job.quantite)):
            contenants_a_placer.append({
                'l': c_l, 'w': c_w, 'h': c_h,
                'dest': job.destination,
                'fs': job.fonction_support,
                'type': job.type_contenant,
                'color': color_map[job.destination]
            })

    # 3. Algorithme Bin Packing 2D (Next Fit Decreasing Height)
    # Tri par hauteur décroissante
    contenants_a_placer.sort(key=lambda x: x['h'], reverse=True)
    
    rectangles_places = []
    current_x = 0
    current_y = 0
    shelf_height = 0
    
    for c in contenants_a_placer:
        # Vérifier si ça rentre dans la rangée actuelle (largeur)
        if current_y + c['w'] > V_W:
            # Nouvelle rangée
            current_x += shelf_height
            current_y = 0
            shelf_height = 0
            
        # Vérifier si ça rentre dans le camion (longueur)
        if current_x + c['l'] > V_L:
            # Ne rentre plus (débordement virtuel pour le visuel)
            c['color'] = 'black' # Marquer en noir les contenants qui dépassent
            
        # Placement
        rectangles_places.append({
            'x': current_x, 'y': current_y,
            'l': c['l'], 'w': c['w'],
            'info': c
        })
        
        current_y += c['w']
        shelf_height = max(shelf_height, c['l'])

    # 4. Génération du graphique Plotly (Vue de dessus)
    fig = go.Figure()

    # Dessiner le contour du camion
    fig.add_shape(type="rect", x0=0, y0=0, x1=V_L, y1=V_W, 
                  line=dict(color="RoyalBlue", width=4), fillcolor="white")

    # Dessiner chaque contenant
    for r in rectangles_places:
        c = r['info']
        hover_text = (f"<b>Contenant:</b> {c['type']}<br>"
                      f"<b>Contenu (FS):</b> {c['fs']}<br>"
                      f"<b>Destination:</b> {c['dest']}<br>"
                      f"<b>Dim:</b> {c['l']}x{c['w']}x{c['h']} mm")
        
        fig.add_trace(go.Scatter(
            x=[r['x'], r['x'] + r['l'], r['x'] + r['l'], r['x'], r['x']],
            y=[r['y'], r['y'], r['y'] + r['w'], r['y'] + r['w'], r['y']],
            fill="toself",
            fillcolor=c['color'],
            mode='lines',
            line=dict(color="black", width=1),
            text=hover_text,
            hoverinfo="text",
            showlegend=False
        ))

    # Configuration des axes
    fig.update_layout(
        title=f"Plan de chargement : {tournee.vehicule_type} ({V_L}x{V_W} mm) - Vue de dessus",
        xaxis=dict(title="Longueur (mm)", range=[0, max(V_L, current_x + shelf_height)]),
        yaxis=dict(title="Largeur (mm)", range=[0, V_W], scaleanchor="x", scaleratio=1),
        margin=dict(l=20, r=20, t=40, b=20),
        height=400
    )
    
    # Ajout d'une légende factice pour les destinations
    for dest, color in color_map.items():
        fig.add_trace(go.Scatter(x=[None], y=[None], mode='markers',
                                    marker=dict(size=10, color=color),
                                    showlegend=True, name=dest))

    return fig
