import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional
import math
import random

# --- STRUCTURES DE DONNÉES ---

@dataclass
class Job:
    id: str
    origine: str
    destination: str
    nature: str
    qte_initiale: float
    reste_a_livrer: float
    poids_u: float
    long_u: float
    larg_u: float
    h_dispo: float
    h_limite: float
    is_sale: bool
    prioritaire: bool

@dataclass
class Tournee:
    id: str
    vehicule_type: str
    h_debut_hsj: float
    h_fin_hsj: float = 0.0
    km_totaux: float = 0.0
    is_sale_tournee: bool = False
    jobs_contenu: List[Dict] = field(default_factory=list)
    remplissage_L: float = 0.0

# --- MOTEUR D'OPTIMISATION HEBDOMADAIRE HOMOGÈNE ---

class MoteurSimulation:
    def __init__(self, data: Dict):
        self.data = data
        self.ref_cont = data['param_contenants'].set_index('libellé').to_dict('index')
        self.ref_veh = data['param_vehicules'].set_index('Types').to_dict('index')
        self.ref_sites = data['param_sites'].set_index('Libellé').to_dict('index')
        self.mat_dist = data['matrice_distance']
        self.mat_dur = data['matrice_duree']
        
        # Paramètres fixes
        self.h_prise_poste = 390  # 06:30
        self.shift_max = 450      # 7h30
        self.nettoyage_t = 20     # minutes
        self.manut_fixe = 10      # minutes (quai/admin)

    def _to_min(self, t):
        if pd.isna(t) or t == "": return 390
        if isinstance(t, datetime) if 'datetime' in globals() else False: return t.hour * 60 + t.minute
        try:
            parts = str(t).split(':')
            return int(parts[0]) * 60 + int(parts[1])
        except: return 390

    def get_travel(self, d, a):
        try:
            return float(self.mat_dist.at[d, a]), float(self.mat_dur.at[d, a])
        except:
            return 0.0, 0.0

    def bin_packing_needed_L(self, job, v_type, qte):
        """Calcule la longueur consommée en respectant la largeur du plateau"""
        l_camion = self.ref_veh[v_type]['dim largeur interne (m)']
        nb_front = max(1, math.floor(l_camion / job.larg_u))
        nb_rangees = math.ceil(qte / nb_front)
        return nb_rangees * job.long_u

    def preparer_jobs(self, jour):
        df = self.data['m_flux']
        col_qte = f"Quantité {jour} "
        if col_qte not in df.columns: return []
        
        mask = (df['Nature du flux (les tournées sont elles à prévoir avec une obligation de transport ou une obligation de passage?)'] == 'Volume') & (df[col_qte] > 0)
        jobs = []
        for idx, row in df[mask].iterrows():
            c = self.ref_cont.get(row['Nature de contenant'], {})
            qte = float(row[col_qte])
            jobs.append(Job(
                id=f"J{idx}", origine=row['Point de départ'], destination=row['Point de destination'],
                nature=row['Nature de contenant'], qte_initiale=qte, reste_a_livrer=qte,
                poids_u=c.get('Poids plein (T)', 0.1), long_u=c.get('dim longueur (m)', 1.2),
                larg_u=c.get('dim largeur (m)', 0.8), h_dispo=self._to_min(row['Heure de mise à disposition min départ']),
                h_limite=self._to_min(row['Heure max de livraison à la destination ']),
                is_sale=(row['Sale / propre'] == 'Sale'),
                prioritaire=(str(row['Urgence / flux prioritaire \n(Oui/Non)']).upper() == "OUI")
            ))
        return jobs

    def construire_journee(self, jour, random_factor=0.2):
        """Génère les tournées d'un jour avec une part d'aléatoire pour l'exploration"""
        jobs = self.preparer_jobs(jour)
        tournees = []
        v_type = "PL 19T" # On travaille sur une flotte homogène de gros porteurs
        v_cfg = self.ref_veh[v_type]
        
        while any(j.reste_a_livrer > 0 for j in jobs):
            dispo = [j for j in jobs if j.reste_a_livrer > 0]
            dispo.sort(key=lambda x: (not x.prioritaire, x.h_limite))
            
            # Sélection aléatoire parmi les meilleurs candidats (GRASP)
            idx_pick = 0 if random.random() > random_factor else random.randint(0, min(len(dispo)-1, 2))
            job_maitre = dispo[idx_pick]
            
            t = Tournee(id=f"T_{jour[:2]}_{len(tournees)+1}", vehicule_type=v_type, 
                        h_debut_hsj=0, is_sale_tournee=job_maitre.is_sale)
            
            # Remplissage opportuniste (Bin Packing Largeur)
            L_max = v_cfg['dim longueur interne (m)']
            for j in dispo:
                if j.is_sale != t.is_sale_tournee: continue
                if self.ref_sites.get(j.destination, {}).get(v_type) != "OUI": continue
                
                l_libre = L_max - t.remplissage_L
                if l_libre <= 0: continue
                
                nb_front = max(1, math.floor(v_cfg['dim largeur interne (m)'] / j.larg_u))
                rangees_possibles = math.floor(l_libre / j.long_u)
                max_qte = rangees_possibles * nb_front
                
                qte_a_charger = min(j.reste_a_livrer, max_qte)
                if qte_a_charger > 0:
                    t.jobs_contenu.append({"job": j, "qte": qte_a_charger})
                    t.remplissage_L += self.bin_packing_needed_L(j, v_type, qte_a_charger)
                    j.reste_a_livrer -= qte_a_charger

            # Calcul itinéraire (Départ HSJ -> Origine -> Destination -> Retour HSJ)
            curr_loc = "HSJ"
            dist_init, dur_init = self.get_travel("HSJ", job_maitre.origine)
            t.h_debut_hsj = max(self.h_prise_poste, job_maitre.h_dispo - dur_init)
            curr_time = t.h_debut_hsj
            
            for jb in t.jobs_contenu:
                d1, t1 = self.get_travel(curr_loc, jb['job'].origine)
                curr_time += t1 + self.manut_fixe
                d2, t2 = self.get_travel(jb['job'].origine, jb['job'].destination)
                curr_time += t2 + (jb['qte'] * 0.5) # 30s par contenant
                t.km_totaux += (d1 + d2)
                curr_loc = jb['job'].destination
            
            d_ret, t_ret = self.get_travel(curr_loc, "HSJ")
            t.h_fin_hsj = curr_time + t_ret
            t.km_totaux += d_ret
            tournees.append(t)
            
        return tournees

    def assigner_rh(self, tournees):
        """Multi-trip : réutilisation des chauffeurs à la minute près (ex: 10h02)"""
        tournees.sort(key=lambda x: x.h_debut_hsj)
        chauffeurs = [] 
        for t in tournees:
            assigne = False
            for c in chauffeurs:
                der = c[-1]
                delai = self.nettoyage_t if (der.is_sale_tournee and not t.is_sale_tournee) else 2
                if der.h_fin_hsj + delai <= t.h_debut_hsj:
                    if (t.h_fin_hsj - c[0].h_debut_hsj) <= self.shift_max:
                        c.append(t)
                        assigne = True
                        break
            if not assigne: chauffeurs.append([t])
        return chauffeurs

    def simuler_semaine_homogene(self, nb_iterations=100):
        meilleure_semaine = None
        meilleur_score = float('inf')
        jours = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]

        for i in range(nb_iterations):
            semaine_test = {}
            pic_flotte = 0
            km_hebdo = 0
            remplissage_cumul = []

            for j in jours:
                t_jour = self.construire_journee(j)
                c_jour = self.assigner_rh(t_jour)
                
                semaine_test[j] = {"tournees": t_jour, "chauffeurs": c_jour}
                pic_flotte = max(pic_flotte, len(c_jour))
                km_hebdo += sum(t.km_totaux for t in t_jour)
                for t in t_jour:
                    v_max_L = self.ref_veh[t.vehicule_type]['dim longueur interne (m)']
                    remplissage_cumul.append(t.remplissage_L / v_max_L)

            # Score : Priorité absolue au nombre de véhicules (le pic de flotte)
            score = (pic_flotte * 1000000) + km_hebdo
            
            if score < meilleur_score:
                meilleur_score = score
                meilleure_semaine = {
                    "detail_jours": semaine_test,
                    "kpis": {
                        "nb_chauffeurs_max_jour": pic_flotte,
                        "nb_tournees": sum(len(d["tournees"]) for d in semaine_test.values()),
                        "distance_totale": km_hebdo,
                        "remplissage_moyen": (sum(remplissage_cumul)/len(remplissage_cumul)*100) if remplissage_cumul else 0
                    }
                }
        return meilleure_semaine

# --- FONCTION D'APPEL ---
def lancer_simulation(data):
    moteur = MoteurSimulation(data)
    return moteur.simuler_semaine_homogene(nb_iterations=2)
