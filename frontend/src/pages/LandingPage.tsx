import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/useAuth';
import { LandingNav } from '../components/landing/LandingNav';
import { Hero } from '../components/landing/Hero';
import { Pipeline } from '../components/landing/Pipeline';
import { Features } from '../components/landing/Features';
import { TimelineDemo } from '../components/landing/TimelineDemo';
import { TechStack } from '../components/landing/TechStack';
import { Footer } from '../components/landing/Footer';
import styles from './LandingPage.module.css';

export function LandingPage() {
  const { isAuthenticated } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    if (isAuthenticated) {
      navigate('/', { replace: true });
    }
  }, [isAuthenticated, navigate]);

  if (isAuthenticated) return null;

  return (
    <div className={styles.root}>
      <LandingNav />
      <main>
        <Hero />
        <Pipeline />
        <Features />
        <TimelineDemo />
        <TechStack />
      </main>
      <Footer />
    </div>
  );
}
