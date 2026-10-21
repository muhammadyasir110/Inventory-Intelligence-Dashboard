import sympy as sp
Q, S, c, sig, u = sp.symbols('Q S c sigma u', positive=True)
z = (S - u) / sig
phi = sp.exp(-z**2/2)/sp.sqrt(2*sp.pi)
Phi = (1 + sp.erf(z/sp.sqrt(2)))/2
L = phi - z*(1 - Phi)
g = c * L / Q          # shortage term with c = c_s * D * sigma
H = sp.hessian(g, (Q, S))
zz = sp.symbols('z', real=True)
phiz = sp.exp(-zz**2/2)/sp.sqrt(2*sp.pi); Phiz = (1+sp.erf(zz/sp.sqrt(2)))/2; Lz = phiz - zz*(1-Phiz)
# claimed closed forms
claims = {
 'g_QQ': 2*c*L/Q**3,
 'g_SS': c*phi/(sig**2*Q),
 'g_QS': c*(1-Phi)/(sig*Q**2),
}
print('dL/dz = -(1-Phi):', sp.simplify(sp.diff(Lz, zz) + (1 - Phiz)) == 0)
print('d2L/dz2 = phi:', sp.simplify(sp.diff(Lz, zz, 2) - phiz) == 0)
for (k, expr), h in zip(claims.items(), (H[0,0], H[1,1], H[0,1])):
    print(k, sp.simplify(h - expr) == 0)
det = sp.simplify(H.det() - c**2/(sig**2*Q**4)*(2*L*phi - (1-Phi)**2))
print('det formula:', det == 0)
# Riccati comparison: b = sqrt(z^2+2) - z ;  need b' <= z b - 1  <=>  z sqrt(z^2+2) <= z^2+1
b = sp.sqrt(zz**2+2) - zz
expr = sp.diff(b, zz) - (zz*b - 1)
print('b\' - (z b - 1) =', sp.simplify(expr))
x = sp.symbols('x', nonnegative=True)
print('(z^2+1)^2 - z^2(z^2+2) =', sp.expand((x**2+1)**2 - x**2*(x**2+2)))
# Mills ratio ODE: R = (1-Phi)/phi satisfies R' = z R - 1
R = (1-Phiz)/phiz
print("R' - (zR - 1) =", sp.simplify(sp.diff(R, zz) - (zz*R - 1)))
