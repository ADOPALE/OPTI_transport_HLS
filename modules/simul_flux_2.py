import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime, timedelta
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
    mutualise_id: Optional[str] = None

@dataclass
class Tournee:
    id: str
    vehicule_type: str
    h_debut_hsj: float
    h_fin_hsj: float = 0.0
    km_totaux: float = 0.0
    is_sale_tournee: bool = False
    jobs_contenu: List[Dict] = field(default_factory=list)
    etapes: List[Dict] = field(default_factory=list)
    remplissage_L: float = 0.0 # Longueur occupée sur le plateau

# --- MOTEUR D'OPTIMISATION ---

class MoteurSimulation:
    def __init__(self, data: Dict):
        self.data = data
        self.ref_cont = data['param_contenants'].set_index('libellé').to_dict('index')
        self.ref_veh = data['param_vehicules'].set_index('Types').to_dict('index')
        self.ref_sites = data['param_sites'].set_index('Libellé').to_dict('index')
        self.mat_dist = data['matrice_distance']
        self.mat_dur = data['matrice_duree']
        
        # Paramètres RH
        self.h_prise_poste = 390  # 06:30
        self.shift_max = 450      # 7h30
        self.nettoyage_t = 20     # minutes
        self.manut_fixe = 10      # minutes (quai/admin)

    def _to_min(self, t):
        if pd.isna(t) or t == "": return 390
        if isinstance(t, datetime): return t.hour * 60 + t.minute
        try:
            h, m = map(int, str(t).split(':')[:2])
            return h * 60 + m
        except: return 390

    def get_travel(self, d, a):
        try: return float(self.mat_dist.at[d, a]), float(self.mat_dur.at[d, a])
        except: return 0.0, 0.0

    def bin_packing_needed_L(self, job, v_type, qte):
        """Calcule la longueur de plancher consommée selon la largeur du camion"""
        l_camion = self.ref_veh[v_type]['dim largeur interne (m)']
        nb_front = max(1, math.floor(l_camion / job.larg_u))
        nb_rangees = math.ceil(qte / nb_front)
        return nb_rangees * job.long_u

    def preparer_jobs(self, jour):
        df = self.data['m_flux']
        col_qte = f"Quantité {jour} "
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

    def construire_solution_unitaire(self, jour, random_factor=0.2):
        """Génère UNE solution complète pour la journée"""
        jobs = self.preparer_jobs(jour)
        tournees = []
        
        while any(j.reste_a_livrer > 0 for j in jobs):
            # Sélection du prochain job (avec une part d'aléatoire pour l'optimisation)
            dispo = [j for j in jobs if j.reste_a_livrer > 0]
            dispo.sort(key=lambda x: (not x.prioritaire, x.h_limite))
            
            # On pioche dans les top candidats au lieu de prendre toujours le 1er (Randomized Greedy)
            idx_pick = 0 if random.random() > random_factor else random.randint(0, min(len(dispo)-1, 2))
            job_maitre = dispo[idx_pick]
            
            # Choix véhicule
            v_type = "PL 19T" # Peut être randomisé aussi pour tester d'autres flottes
            v_cfg = self.ref_veh[v_type]
            
            t = Tournee(id=f"T_{len(tournees)+1}", vehicule_type=v_type, h_debut_hsj=0, is_sale_tournee=job_maitre.is_sale)
            
            # Remplissage
            L_max = v_cfg['dim longueur interne (m)']
            poids_max = v_cfg['Poids max chargement']
            
            # Ajout opportuniste
            for j in dispo:
                if j.is_sale != t.is_sale_tournee: continue
                if self.ref_sites.get(j.destination, {}).get(v_type) != "OUI": continue
                
                # Combien peut-on en mettre ?
                l_libre = L_max - t.remplissage_L
                if l_libre <= 0: continue
                
                # Test Bin Packing
                nb_front = max(1, math.floor(v_cfg['dim largeur interne (m)'] / j.larg_u))
                rangees_possibles = math.floor(l_libre / j.long_u)
                max_qte_geometrie = rangees_possibles * nb_front
                
                qte_a_charger = min(j.reste_a_livrer, max_qte_geometrie)
                
                if qte_a_charger > 0:
                    t.jobs_contenu.append({"job": j, "qte": qte_a_charger})
                    t.remplissage_L += self.bin_packing_needed_L(j, v_type, qte_a_charger)
                    j.reste_a_livrer -= qte_a_charger

            # Calcul des temps et distances
            curr_loc = "HSJ"
            curr_time = max(self.h_prise_poste, job_maitre.h_dispo - 30) # Estimation départ
            t.h_debut_hsj = curr_time
            
            for jb in t.jobs_contenu:
                dist, dur = self.get_travel(curr_loc, jb['job'].origine)
                curr_time += dur + self.manut_fixe
                dist_liv, dur_liv = self.get_travel(jb['job'].origine, jb['job'].destination)
                curr_time += dur_liv + (jb['qte'] * 0.5) # +30s par chariot
                t.km_totaux += (dist + dist_liv)
                curr_loc = jb['job'].destination
            
            # Retour HSJ
            d_ret, dur_ret = self.get_travel(curr_loc, "HSJ")
            t.h_fin_hsj = curr_time + dur_ret
            t.km_totaux += d_ret
            tournees.append(t)
            
        return tournees

    def optimiser_journee(self, jour, nb_iterations=100):
        meilleure_sol = None
        meilleur_score = float('inf')
        
        for _ in range(nb_iterations):
            sol = self.construire_solution_unitaire(jour)
            # Fitness : Coût = (Nb Camions * 500) + (Km Totaux * 2)
            score = (len(sol) * 500) + (sum(t.km_totaux for t in sol) * 2)
            
            if score < meilleur_score:
                meilleur_score = score
                meilleure_sol = sol
                
        return meilleure_sol

    def assigner_rh(self, tournees):
        tournees.sort(key=lambda x: x.h_debut_hsj)
        chauffeurs = [] # Liste de listes de tournees
        
        for t in tournees:
            place_trouvee = False
            for c in chauffeurs:
                dernier_t = c[-1]
                delai = self.nettoyage_t if (dernier_t.is_sale_tournee and not t.is_sale_tournee) else 2
                
                if dernier_t.h_fin_hsj + delai <= t.h_debut_hsj:
                    if (t.h_fin_hsj - c[0].h_debut_hsj) <= self.shift_max:
                        c.append(t)
                        place_trouvee = True
                        break
            if not place_trouvee:
                chauffeurs.append([t])
        return chauffeurs

def lancer_simulation(data):
    moteur = MoteurSimulation(data)
    resultats = {}
    for j in ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]:
        t_opti = moteur.optimiser_journee(j, nb_iterations=100)
        c_opti = moteur.assigner_rh(t_opti)
        resultats[j] = {"tournees": t_opti, "chauffeurs": c_opti}
    return resultats
